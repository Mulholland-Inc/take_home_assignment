"""Atlas — a minimal single-tenant ontology engine on Postgres.

Atlas does write-and-store. Hard-id dedup at upsert time on Unique fields.
Fuzzy-name fields (`Fuzzy[...]`) are not deduplicated automatically — they
surface as a soft warning on upsert so the agent can decide.

Python API:
  register(type, json_schema)         — declare an entity class
  upsert(type, data, resolve_by=)     — insert or update by exact id match
  upsert_many(type, [data,…], …)      — same, batched
  link(s, predicate, o, data=, …)     — add a relation
  query(type, where=)                 — fetch entities by jsonb filter

CLI:
  python -m atlas export <client> --out <snapshot>
  python -m atlas restore --from <snapshot>
  python -m atlas web

Each client lives in example/<client>/ and provides one module:
  ontology.py  — REGISTRY, PREDICATES, register_all(atlas)
"""

from __future__ import annotations

import inspect
import json
import uuid
from typing import Annotated, Iterable, Optional

import jsonschema
import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, ValidationError


from atlas.markers import (
    _FUZZY_MARKER, _UNIQUE_MARKER, _RefMarker,
    Fuzzy, Hint, Ref, Unique,
    Money, SqFt, USAddress, USState, USZip, Year,
    Percentage, Ratio, Count,
    Email, PhoneE164, URL,
    CurrencyCode, EIN,
)


def _has_marker(ann, marker: str) -> bool:
    """True if `marker` (string) appears in any Annotated metadata layer
    inside `ann`, recursing through Optional/Union, nested Annotated, and
    Annotated objects stored as metadata items (e.g.
    `Annotated[Unique[str], Fuzzy[str]]`, where `Fuzzy[str]` is itself an
    Annotated and is held as a metadata entry of the outer Annotated)."""
    import typing
    meta = getattr(ann, "__metadata__", None)
    if meta is not None:
        for m in meta:
            if m == marker:
                return True
            # An Annotated-shaped metadata item (Fuzzy[str], Unique[str], …)
            if hasattr(m, "__metadata__") and _has_marker(m, marker):
                return True
        args = typing.get_args(ann)
        return bool(args) and _has_marker(args[0], marker)
    if typing.get_origin(ann) is typing.Union:
        return any(_has_marker(a, marker)
                   for a in typing.get_args(ann)
                   if a is not type(None))
    return False


def _marked_fields(model: type[BaseModel], marker: str) -> list[str]:
    def has(field) -> bool:
        for m in field.metadata:
            if m == marker:
                return True
            # Nested Annotated stored as a metadata item (this is how
            # `Annotated[Unique[str], Fuzzy[str]]` shows up after pydantic
            # flattens the outer layer).
            if hasattr(m, "__metadata__") and _has_marker(m, marker):
                return True
        return _has_marker(field.annotation, marker)
    return [name for name, field in model.model_fields.items() if has(field)]


def unique_fields(model: type[BaseModel]) -> list[str]:
    """Names of fields on `model` annotated as Unique[...] (including
    Optional[Unique[...]]). Order matches field declaration order."""
    return _marked_fields(model, _UNIQUE_MARKER)


def fuzzy_fields(model: type[BaseModel]) -> list[str]:
    """Names of fields on `model` annotated as Fuzzy[...] — used by
    monorail to surface near-duplicate warnings at write time. A field
    can be both Unique and Fuzzy; that's the typical name-id pattern."""
    return _marked_fields(model, _FUZZY_MARKER)


def _find_ref_marker(ann) -> _RefMarker | None:
    """Walk into Annotated/Optional/Union/list layers to find a
    `_RefMarker`. Returns None if the field isn't (or doesn't contain)
    a Ref."""
    import typing
    # Direct _RefMarker (defensive)
    if isinstance(ann, _RefMarker):
        return ann
    # Annotated[...]: scan metadata, then descend the wrapped type
    meta = getattr(ann, "__metadata__", None)
    if meta is not None:
        for m in meta:
            if isinstance(m, _RefMarker):
                return m
            if hasattr(m, "__metadata__"):
                hit = _find_ref_marker(m)
                if hit is not None:
                    return hit
        args = typing.get_args(ann)
        if args:
            return _find_ref_marker(args[0])
        return None
    origin = typing.get_origin(ann)
    # Optional / Union[A, B]: try each arm
    if origin is typing.Union:
        for a in typing.get_args(ann):
            if a is type(None):
                continue
            hit = _find_ref_marker(a)
            if hit is not None:
                return hit
        return None
    # list[Ref[...]] / tuple[Ref[...]] / set[...]
    if origin in (list, tuple, set):
        for a in typing.get_args(ann):
            hit = _find_ref_marker(a)
            if hit is not None:
                return hit
    return None


def ref_fields(model: type[BaseModel]) -> list[tuple[str, tuple[str, ...]]]:
    """Names of fields on `model` annotated as Ref[...] paired with their
    allowed target type names. Order matches field declaration order.

    Looks in BOTH `field.metadata` (where pydantic stashes flattened
    Annotated markers) and `field.annotation` (where they remain when
    wrapped by Optional/list/etc.). For a `list[Ref[Property]]` field
    the same `(name, targets)` is reported once — the caller iterates
    the list at validation time."""
    out: list[tuple[str, tuple[str, ...]]] = []
    for name, field in model.model_fields.items():
        marker: _RefMarker | None = None
        for m in field.metadata:
            if isinstance(m, _RefMarker):
                marker = m
                break
            if hasattr(m, "__metadata__"):
                marker = _find_ref_marker(m)
                if marker:
                    break
        if marker is None:
            marker = _find_ref_marker(field.annotation)
        if marker is not None:
            out.append((name, marker.target_names))
    return out


def _field_is_list_ref(model: type[BaseModel], name: str) -> bool:
    import typing
    ann = model.model_fields[name].annotation
    while typing.get_origin(ann) is typing.Union:
        ann = next((a for a in typing.get_args(ann) if a is not type(None)), ann)
    return typing.get_origin(ann) in (list, tuple, set)


def _apply_before_validators(
    model: type[BaseModel], field_name: str, value,
):
    """Run any `BeforeValidator` declared on a single field over `value`.
    Used by `atlas.query` so that a search term passes through the same
    normalization that the stored value did at write time (e.g. address
    canonicalization). Returns `value` unchanged if no BeforeValidator
    is declared on the field."""
    from pydantic.functional_validators import BeforeValidator
    import typing

    if value is None:
        return value
    field = model.model_fields.get(field_name)
    if field is None:
        return value

    def walk(items) -> list:
        out = []
        for m in items:
            if isinstance(m, BeforeValidator):
                out.append(m)
            elif hasattr(m, "__metadata__"):
                out.extend(walk(m.__metadata__))
        return out

    validators = walk(field.metadata)
    # Also peek inside Optional/Union annotations for nested Annotated.
    ann = field.annotation
    if typing.get_origin(ann) is typing.Union:
        for arg in typing.get_args(ann):
            if arg is type(None):
                continue
            inner_meta = getattr(arg, "__metadata__", None)
            if inner_meta:
                validators.extend(walk(inner_meta))

    for v in validators:
        try:
            value = v.func(value)
        except Exception:
            # If normalization fails (e.g. unparseable address), fall
            # back to the raw search value; atlas_norm still handles
            # case/whitespace.
            return value
    return value


class SchemaError(Exception):
    """Raised when an entity's data fails its declared JSON Schema."""


class Atlas:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        # JSON Schema dicts (what clients and snapshots read).
        self._schema_cache: dict[str, dict | None] = {}
        # Pydantic classes, when a type was registered as a model. Used by
        # _validate for fast C-speed validation. Types registered as raw
        # dicts (e.g. snapshot restore) have no class and fall back to
        # jsonschema.validate.
        self._model_cache: dict[str, type[BaseModel] | None] = {}

    # ---- Ontology (T-Box) ----

    def register(self, type: str, json_schema: dict | type[BaseModel]) -> None:
        """Declare an entity class. Accepts either a JSON Schema dict or a
        Pydantic BaseModel subclass; in the model case the JSON Schema is
        derived via `model_json_schema()` (with any `unique` carried via
        `model_config.json_schema_extra`) and the class is cached for
        validation."""
        if inspect.isclass(json_schema) and issubclass(json_schema, BaseModel):
            model_cls: type[BaseModel] | None = json_schema
            schema_dict = model_cls.model_json_schema()
        else:
            model_cls = None
            schema_dict = json_schema  # type: ignore[assignment]
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into entity_type (type, json_schema)
                values (%s, %s::jsonb)
                on conflict (type) do update set json_schema = excluded.json_schema
                """,
                (type, json.dumps(schema_dict)),
            )
        self._schema_cache[type] = schema_dict
        self._model_cache[type] = model_cls

    def schema_for(self, type: str) -> dict | None:
        if type in self._schema_cache:
            return self._schema_cache[type]
        with self.conn.cursor() as cur:
            cur.execute("select json_schema from entity_type where type=%s", (type,))
            row = cur.fetchone()
        schema = row[0] if row else None
        self._schema_cache[type] = schema
        return schema

    def _validate(self, type: str, data: dict) -> dict:
        """Validate `data` against the type's schema and return its
        canonical (normalized) form. BeforeValidators run during
        `model_validate`, so the returned dict reflects whatever
        normalization the model declared (e.g. address parsing,
        US-state code uppercasing). Only fields the caller supplied are
        carried through (`exclude_unset=True`) so the caller's intent
        about field presence is preserved.

        Ref[...] fields are additionally checked against the live
        `entity` table: the supplied uuid must exist and its entity.type
        must be one of the declared targets.

        Types registered as a raw JSON Schema (no Pydantic class) can't
        normalize — `data` is returned unchanged after jsonschema
        validation. That path is only used on snapshot restore.
        """
        model_cls = self._model_cache.get(type)
        if model_cls is not None:
            try:
                obj = model_cls.model_validate(data)
            except ValidationError as e:
                raise SchemaError(f"{type} entity failed schema: {e}") from e
            normalized = obj.model_dump(mode="json", exclude_unset=True)
            self._check_refs(type, model_cls, normalized)
            return normalized
        schema = self.schema_for(type)
        if schema is None:
            return data
        try:
            jsonschema.validate(instance=data, schema=schema)
        except jsonschema.ValidationError as e:
            raise SchemaError(f"{type} entity failed schema: {e.message}") from e
        return data

    def _check_refs(
        self,
        type: str,
        model_cls: type[BaseModel],
        data: dict,
    ) -> None:
        """Verify each Ref[...] field on `data` points at an existing
        entity whose type is in the field's allowed targets. Raises
        SchemaError on the first miss with all offenders aggregated."""
        refs = ref_fields(model_cls)
        if not refs:
            return
        errors: list[str] = []
        with self.conn.cursor() as cur:
            for fname, targets in refs:
                if fname not in data or data[fname] is None:
                    continue
                value = data[fname]
                values = value if _field_is_list_ref(model_cls, fname) else [value]
                for v in values:
                    try:
                        ref_id = uuid.UUID(str(v))
                    except (ValueError, TypeError):
                        errors.append(f"{fname}: {v!r} is not a uuid")
                        continue
                    cur.execute("select type from entity where id = %s", (ref_id,))
                    row = cur.fetchone()
                    if row is None:
                        errors.append(f"{fname}: no entity with id {ref_id}")
                        continue
                    if row[0] not in targets:
                        errors.append(
                            f"{fname}: {ref_id} is a {row[0]} but must be "
                            f"one of {'|'.join(targets)}"
                        )
        if errors:
            raise SchemaError(f"{type} ref check failed: " + "; ".join(errors))

    def id_fields_for(
        self,
        type: str,
        resolve_by: list[str] | str | None,
    ) -> list[str]:
        """Hard-identifier fields for this type — union of the model's
        `Unique[...]` fields and the caller's `resolve_by`."""
        model_cls = self._model_cache.get(type)
        out: list[str] = unique_fields(model_cls) if model_cls else []
        if resolve_by:
            extras = resolve_by if isinstance(resolve_by, list) else [resolve_by]
            for f in extras:
                if f not in out:
                    out.append(f)
        return out

    # ---- Instances (A-Box) ----

    def upsert(
        self,
        type: str,
        data: dict,
        *,
        reason: str,
        sources: list[uuid.UUID] | None = None,
        resolve_by: list[str] | str | None = None,
    ) -> uuid.UUID:
        return self.upsert_many(
            type, [data],
            reason=reason, sources=sources, resolve_by=resolve_by,
        )[0]

    def upsert_many(
        self,
        type: str,
        datas: list[dict],
        *,
        reason: str,
        sources: list[uuid.UUID] | None = None,
        resolve_by: list[str] | str | None = None,
    ) -> list[uuid.UUID]:
        """Insert each record, deduplicating only on hard ids
        (`unique` ∪ `resolve_by`). Records without an exact id match
        get inserted as new rows.

        Within a batch, items sharing an id key resolve to the same DB row."""
        if not datas:
            return []

        # Normalize on validate (USAddress canonicalization, USState
        # uppercasing, Decimal/date round-tripping). `datas` is replaced
        # with the canonical dicts so id_fields_for, _match_exact, and
        # storage all see the same canonical form.
        datas = [self._validate(type, d) for d in datas]

        if not reason:
            raise ValueError("reason is required on upsert")
        id_fields = self.id_fields_for(type, resolve_by)
        sources = sources or []
        primary_source = sources[0] if sources else None
        n = len(datas)
        ids: list[uuid.UUID | None] = [None] * n

        # In-batch: items sharing ANY id field value collapse to one DB row,
        # so long as no other id field both populate disagrees. Same rule
        # _match_exact applies to DB candidates.
        merge_into: list[int | None] = [None] * n
        for i in range(n):
            if merge_into[i] is not None:
                continue
            for j in range(i):
                if merge_into[j] is not None:
                    continue  # j is already a merged sibling, skip
                if _shares_id(datas[i], datas[j], id_fields) and not _conflict(
                    datas[i], datas[j], id_fields
                ):
                    merge_into[i] = j
                    break

        # First pass: resolve representatives (each unique key) against the DB.
        with self.conn.cursor() as cur:
            for i, d in enumerate(datas):
                if merge_into[i] is not None:
                    continue
                # Serialize concurrent upserts that share an id-field value.
                # Without this, two threads upserting the same logical entity
                # both SELECT (miss), both INSERT, producing duplicate rows
                # because the Unique marker has no DB-side index. The lock
                # is transaction-scoped, released at commit. Lock keys are
                # taken in sorted order to avoid deadlocks when an upsert
                # touches multiple id fields.
                for f in sorted(id_fields):
                    v = d.get(f)
                    if v is None:
                        continue
                    if isinstance(v, str):
                        key = f"atlas-upsert:{type}:{f}={_norm(v)}"
                    else:
                        key = f"atlas-upsert:{type}:{f}={json.dumps(v, sort_keys=True, default=str)}"
                    cur.execute(
                        "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (key,),
                    )
                hit = self._match_exact(cur, type, d, id_fields)
                if hit is not None:
                    hit_id, existing_data = hit
                    payload = _strip_nulls(d)
                    kept, conflicts = _split_conflicts(
                        payload, existing_data, id_fields
                    )
                    if conflicts:
                        existing_src = _primary_source_for_entity(cur, hit_id)
                        for field, incoming_value in conflicts:
                            cur.execute(
                                """
                                insert into entity_conflict
                                  (entity_id, field, existing_value, incoming_value,
                                   existing_source_id, incoming_source_id, resolution)
                                values (%s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
                                """,
                                (
                                    hit_id,
                                    field,
                                    json.dumps(existing_data.get(field)),
                                    json.dumps(incoming_value),
                                    existing_src,
                                    primary_source,
                                    "kept_existing",
                                ),
                            )
                    cur.execute(
                        """
                        update entity
                           set data       = data || %s::jsonb,
                               updated_at = now()
                         where id = %s
                        """,
                        (json.dumps(kept), hit_id),
                    )
                    _log_provenance(cur, hit_id, kept, primary_source)
                    _link_entity_sources(cur, hit_id, sources)
                    _log_audit(cur, "update", reason, entity_id=hit_id)
                    ids[i] = hit_id
                else:
                    cur.execute(
                        "insert into entity (type, data) values (%s, %s::jsonb) returning id",
                        (type, json.dumps(d)),
                    )
                    new_id = cur.fetchone()[0]
                    _log_provenance(cur, new_id, _strip_nulls(d), primary_source)
                    _link_entity_sources(cur, new_id, sources)
                    _log_audit(cur, "create", reason, entity_id=new_id)
                    ids[i] = new_id

        # Scatter representative ids to merged siblings.
        for i in range(n):
            if merge_into[i] is not None:
                ids[i] = ids[merge_into[i]]

        return ids  # type: ignore[return-value]

    def query(self, type: str, where: dict | None = None) -> list[dict]:
        """Fetch entities of `type` whose stored data matches `where`.

        String values are pushed through any BeforeValidator the field
        carries (so a search for `address='5404 South 122nd East
        Avenue'` is canonicalized to the same form atlas stored under).
        After normalization the comparison is `atlas_norm` so
        leftover case/whitespace differences still match. Non-string
        values use jsonb containment (`data @> ...`)."""
        with self.conn.cursor(row_factory=dict_row) as cur:
            if not where:
                cur.execute("select * from entity where type = %s", (type,))
                return cur.fetchall()
            model = self._model_cache.get(type)
            clauses: list[str] = ["type = %s"]
            params: list = [type]
            jsonb_terms: dict = {}
            for k, v in where.items():
                if isinstance(v, str):
                    norm = _apply_before_validators(model, k, v) if model else v
                    clauses.append("atlas_norm(data->>%s) = atlas_norm(%s)")
                    params.extend([k, norm])
                else:
                    jsonb_terms[k] = v
            if jsonb_terms:
                clauses.append("data @> %s::jsonb")
                params.append(json.dumps(jsonb_terms))
            cur.execute(
                "select * from entity where " + " and ".join(clauses),
                tuple(params),
            )
            return cur.fetchall()

    def source(
        self,
        hash: str,
        *,
        kind: str = "document",
        uri: str | None = None,
        data: dict | None = None,
        metadata: dict | None = None,
    ) -> uuid.UUID:
        """Upsert an `entity_source` row keyed by content hash. Idempotent:
        re-ingesting the same payload merges data + metadata into the
        existing row.

        `kind` is the source category — 'document' is the default; future
        kinds (chat_message, api_response, user_input, …) plug in by
        passing a new value here. `uri` is informational (e.g.
        `file://path.pdf`). `data` carries the inline payload when there
        is no file — e.g. a chat message body, an API response, a typed
        prompt. `metadata` is descriptive (filename, size, page_count, …)."""
        if not hash:
            raise ValueError("source(hash=) is required")
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into entity_source (hash, kind, uri, data, metadata)
                values (%s, %s, %s, %s::jsonb, %s::jsonb)
                on conflict (hash) do update set
                  kind     = coalesce(excluded.kind, entity_source.kind),
                  uri      = coalesce(excluded.uri, entity_source.uri),
                  data     = entity_source.data || excluded.data,
                  metadata = entity_source.metadata || excluded.metadata
                returning id
                """,
                (hash, kind, uri, json.dumps(data or {}), json.dumps(metadata or {})),
            )
            return cur.fetchone()[0]

    def provenance(
        self,
        entity_id: uuid.UUID,
        field: str | None = None,
    ) -> list[dict]:
        """Return provenance rows for an entity, newest first. Filter by
        field if given."""
        with self.conn.cursor(row_factory=dict_row) as cur:
            if field is None:
                cur.execute(
                    "select * from entity_provenance where entity_id = %s "
                    "order by recorded_at desc",
                    (entity_id,),
                )
            else:
                cur.execute(
                    "select * from entity_provenance "
                    "where entity_id = %s and field = %s "
                    "order by recorded_at desc",
                    (entity_id, field),
                )
            return cur.fetchall()

    def correct(
        self,
        id: uuid.UUID,
        fields: dict,
        *,
        reason: str,
        sources: list[uuid.UUID] | None = None,
    ) -> None:
        """Overwrite fields on an existing entity, bypassing the
        kept_existing conflict policy. Validates the merged record against
        the type's schema. Each corrected field is appended to
        entity_provenance; sources are linked via entity_source_link and
        the action is logged in entity_audit with `reason`.

        Reserved for deliberate corrections; ordinary upserts should go
        through `upsert()` so disagreements land in entity_conflict.
        """
        if not fields:
            return
        if not reason:
            raise ValueError("reason is required on correct")
        sources = sources or []
        primary_source = sources[0] if sources else None
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select type, data from entity where id = %s", (id,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"no entity with id {id}")
            merged = {**row["data"], **fields}
            normalized_full = self._validate(row["type"], merged)
            normalized_delta = {k: normalized_full.get(k) for k in fields}
            cur.execute(
                "update entity set data = data || %s::jsonb, updated_at = now() where id = %s",
                (json.dumps(normalized_delta), id),
            )
            _log_provenance(cur, id, normalized_delta, primary_source)
            _link_entity_sources(cur, id, sources)
            _log_audit(cur, "update", reason, entity_id=id)

    # ---- internals ----

    @staticmethod
    def _match_exact(
        cur: psycopg.Cursor,
        type: str,
        data: dict,
        id_fields: list[str],
    ) -> tuple[uuid.UUID, dict] | None:
        """OR-match on id_fields, conflict-check on the rest. First
        non-conflicting candidate wins.

        Lets a Tenant declared with `unique`=[tenant_code, name] collapse
        when names match but bail out when names match and tenant_codes
        disagree (different real tenants sharing a brand)."""
        keys = [k for k in id_fields if k in data and data[k] is not None]
        if not keys:
            return None
        or_conds: list[str] = []
        params: list = [type]
        for k in keys:
            v = data[k]
            if isinstance(v, str):
                or_conds.append("atlas_norm(data->>%s) = atlas_norm(%s)")
                params.extend([k, v])
            else:
                or_conds.append("data->%s = %s::jsonb")
                params.extend([k, json.dumps(v)])
        sql = (
            f"select id, data from entity "
            f"where type = %s and ({' or '.join(or_conds)})"
        )
        cur.execute(sql, params)
        rows = cur.fetchall()
        for row_id, row_data in rows:
            if not _conflict(data, row_data, id_fields):
                return row_id, row_data
        return None


def _strip_nulls(d: dict) -> dict:
    """Drop nulls from an update payload so they don't overwrite existing fields."""
    return {k: v for k, v in d.items() if v is not None}


def _norm(s) -> str | None:
    if not isinstance(s, str):
        return None
    return " ".join(s.lower().split())


def _shares_id(a: dict, b: dict, id_fields: list[str]) -> bool:
    """True iff a and b have at least one id field set to the same value
    (case/space-insensitive for strings)."""
    for f in id_fields:
        av, bv = a.get(f), b.get(f)
        if av is None or bv is None:
            continue
        if isinstance(av, str) and isinstance(bv, str):
            if _norm(av) == _norm(bv):
                return True
        elif av == bv:
            return True
    return False


def _log_provenance(
    cur: psycopg.Cursor,
    entity_id: uuid.UUID,
    payload: dict,
    source_id: uuid.UUID | None,
) -> None:
    """Append one provenance row per field actually written."""
    if not payload:
        return
    rows = [(entity_id, k, json.dumps(v), source_id) for k, v in payload.items()]
    cur.executemany(
        "insert into entity_provenance (entity_id, field, value, source_id) "
        "values (%s, %s, %s::jsonb, %s)",
        rows,
    )


def _log_audit(
    cur: psycopg.Cursor,
    op: str,
    reason: str,
    *,
    entity_id: uuid.UUID,
) -> None:
    cur.execute(
        "insert into entity_audit (op, entity_id, reason) values (%s, %s, %s)",
        (op, entity_id, reason),
    )


def _link_entity_sources(
    cur: psycopg.Cursor,
    entity_id: uuid.UUID,
    sources: list[uuid.UUID],
) -> None:
    if not sources:
        return
    rows = [(entity_id, s) for s in sources if s is not None]
    if not rows:
        return
    cur.executemany(
        "insert into entity_source_link (entity_id, source_id) values (%s, %s) "
        "on conflict do nothing",
        rows,
    )


def _primary_source_for_entity(
    cur: psycopg.Cursor,
    entity_id: uuid.UUID,
) -> uuid.UUID | None:
    """Pick a representative source already attached to this entity, for
    use as the `existing_source_id` on conflict rows."""
    cur.execute(
        "select source_id from entity_source_link where entity_id = %s limit 1",
        (entity_id,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _split_conflicts(
    incoming: dict,
    existing: dict,
    id_fields: list[str],
) -> tuple[dict, list[tuple[str, object]]]:
    """Partition incoming non-null fields into (kept, conflicts).

    A conflict is an incoming non-null value that disagrees with an existing
    non-null value on a non-id field. id fields are skipped because the
    candidate was already accepted by `_match_exact`'s conflict check on
    those fields. List fields are compared element-wise; everything else
    by equality (no string normalisation — exact match).
    """
    kept: dict = {}
    conflicts: list[tuple[str, object]] = []
    id_set = set(id_fields)
    for k, v in incoming.items():
        if k in id_set:
            kept[k] = v
            continue
        ev = existing.get(k) if isinstance(existing, dict) else None
        if ev is None or ev == v:
            kept[k] = v
        else:
            conflicts.append((k, v))
    return kept, conflicts


def _conflict(new: dict, existing: dict, id_fields: list[str]) -> bool:
    """True iff new and existing agree on at least one id field but disagree
    on another id field both rows populate. Used to reject write-time merges
    that would conflate distinct real-world entities."""
    for f in id_fields:
        nv = new.get(f)
        ev = existing.get(f) if isinstance(existing, dict) else None
        if nv is None or ev is None:
            continue
        if isinstance(nv, str) and isinstance(ev, str):
            if _norm(nv) != _norm(ev):
                return True
        elif nv != ev:
            return True
    return False


# ---- Snapshot export / restore (msgpack + gzip) -------------------------
#
# Per-client snapshot of the entity graph: schemas (T-Box), entities, and
# the relations that fall entirely between this client's types.
# msgpack keeps the on-disk format binary and fast to (de)serialise;
# gzip gives 3–5x extra compression on top of msgpack's already-compact
# encoding.
#
# Format: one msgpack object per record, gzip-streamed. Records are tagged
# by `kind` (`schema`, `entity`, `relation`) so the loader can dispatch.

def _client_types(client: str) -> list[str]:
    """Type names registered by example.<client>.ontology."""
    return list(_client_module(client, "ontology").REGISTRY.keys())


def export(atlas: "Atlas", client: str, out_path: str) -> dict[str, int]:
    """Dump this client's schemas + entities + source attribution + audit
    log to a msgpack-gzip file. Returns a record-count summary."""
    import gzip
    import msgpack

    types = _client_types(client)
    counts = {"schemas": 0, "sources": 0, "entities": 0, "audits": 0}

    with gzip.open(out_path, "wb") as f, atlas.conn.cursor() as cur:
        def pack(obj: dict) -> None:
            f.write(msgpack.packb(obj, datetime=True))

        cur.execute(
            "select type, json_schema from entity_type where type = any(%s)",
            (types,),
        )
        for type_, schema in cur.fetchall():
            pack({"kind": "schema", "type": type_, "json_schema": schema})
            counts["schemas"] += 1

        cur.execute(
            """
            select id, hash, kind, uri, data, metadata, ingested_at from entity_source
            where id in (
              select source_id from entity_source_link
               where entity_id in (select id from entity where type = any(%s))
            )
            """,
            (types,),
        )
        for row in cur.fetchall():
            pack({
                "kind": "source",
                "id": str(row[0]),
                "hash": row[1],
                "source_kind": row[2],
                "uri": row[3],
                "data": row[4],
                "metadata": row[5],
                "ingested_at": row[6],
            })
            counts["sources"] += 1

        cur.execute(
            "select id, type, data, created_at, updated_at "
            "from entity where type = any(%s)",
            (types,),
        )
        for row in cur.fetchall():
            pack({
                "kind": "entity",
                "id": str(row[0]),
                "type": row[1],
                "data": row[2],
                "created_at": row[3],
                "updated_at": row[4],
            })
            counts["entities"] += 1

        cur.execute(
            "select entity_id, source_id from entity_source_link "
            "where entity_id in (select id from entity where type = any(%s))",
            (types,),
        )
        for entity_id, source_id in cur.fetchall():
            pack({"kind": "entity_source_link",
                  "entity_id": str(entity_id),
                  "source_id": str(source_id)})

        cur.execute(
            "select id, op, entity_id, reason, at from entity_audit "
            "where entity_id in (select id from entity where type = any(%s))",
            (types,),
        )
        for row in cur.fetchall():
            pack({
                "kind": "audit",
                "id": str(row[0]),
                "op": row[1],
                "entity_id": str(row[2]) if row[2] else None,
                "reason": row[3],
                "at": row[4],
            })
            counts["audits"] += 1

    return counts


def restore(atlas: "Atlas", in_path: str) -> dict[str, int]:
    """Load a snapshot back into atlas. Preserves ids and timestamps.
    Skips rows whose id already exists (idempotent reload)."""
    import gzip
    import msgpack

    counts = {"schemas": 0, "sources": 0, "entities": 0, "audits": 0}

    with gzip.open(in_path, "rb") as f, atlas.conn.cursor() as cur:
        unpacker = msgpack.Unpacker(f, raw=False, timestamp=3)
        for record in unpacker:
            kind = record["kind"]
            if kind == "source":
                cur.execute(
                    """
                    insert into entity_source (id, hash, kind, uri, data, metadata, ingested_at)
                    values (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                    on conflict (hash) do nothing
                    """,
                    (
                        record["id"], record["hash"], record["source_kind"],
                        record.get("uri"),
                        json.dumps(record.get("data") or {}),
                        json.dumps(record.get("metadata") or {}),
                        record["ingested_at"],
                    ),
                )
                if cur.rowcount:
                    counts["sources"] += 1
            elif kind == "schema":
                cur.execute(
                    """
                    insert into entity_type (type, json_schema)
                    values (%s, %s::jsonb)
                    on conflict (type) do update set json_schema = excluded.json_schema
                    """,
                    (record["type"], json.dumps(record["json_schema"])),
                )
                counts["schemas"] += 1
            elif kind == "entity":
                cur.execute(
                    """
                    insert into entity (id, type, data, created_at, updated_at)
                    values (%s, %s, %s::jsonb, %s, %s)
                    on conflict (id) do nothing
                    """,
                    (
                        record["id"], record["type"], json.dumps(record["data"]),
                        record["created_at"], record["updated_at"],
                    ),
                )
                if cur.rowcount:
                    counts["entities"] += 1
            elif kind == "entity_source_link":
                cur.execute(
                    "insert into entity_source_link (entity_id, source_id) values (%s, %s) "
                    "on conflict do nothing",
                    (record["entity_id"], record["source_id"]),
                )
            elif kind == "audit":
                cur.execute(
                    "insert into entity_audit (id, op, entity_id, reason, at) "
                    "values (%s, %s, %s, %s, %s) on conflict do nothing",
                    (record["id"], record["op"], record.get("entity_id"),
                     record["reason"], record["at"]),
                )
                if cur.rowcount:
                    counts["audits"] += 1
    atlas.conn.commit()
    return counts


# ---- CLI dispatcher ------------------------------------------------------

def _client_module(client: str, submodule: str):
    """Import <client>.<submodule> — each client is its own installable package."""
    import importlib
    return importlib.import_module(f"{client}.{submodule}")


def _launch_pgweb(port: str) -> None:
    """Boot the embedded server if needed, then exec pgweb against it.
    pgweb runs in the foreground; Ctrl-C drops back to the shell. The
    server keeps running in the background after pgweb exits."""
    import shutil
    import subprocess
    import urllib.parse

    pgweb = shutil.which("pgweb")
    if pgweb is None:
        raise SystemExit(
            "pgweb not found on PATH. Install it: brew install pgweb"
        )

    conn = connect()
    info = conn.info
    socket_dir = info.host
    dbname = info.dbname
    user = info.user
    conn.close()

    url = (
        f"postgres://{user}@/{dbname}"
        f"?host={urllib.parse.quote(socket_dir, safe='')}"
        f"&sslmode=disable"
    )
    print(f"pgweb -> http://localhost:{port}")
    subprocess.run([pgweb, "--url", url, "--listen", port], check=False)


# ---- Connection lifecycle -----------------------------------------------

_DEFAULT_LOCAL_DSN = "postgresql://atlas:atlas@localhost:5432/atlas"


def connect() -> psycopg.Connection:
    """Open a connection to the atlas database. Pure psycopg.connect —
    no DDL, no schema apply.

    `ATLAS_DSN` selects the target — in prod it's Cloud SQL via the
    auth proxy; locally it's the Postgres 18 container defined in
    `docker-compose.yml` at the repo root (default DSN
    `postgresql://atlas:atlas@localhost:5432/atlas` when unset).

    The atlas schema is applied separately via `bootstrap()`, which each
    entrypoint should call once at startup. `connect()` itself is safe
    to call concurrently from many threads.
    """
    import os

    dsn = os.getenv("ATLAS_DSN", _DEFAULT_LOCAL_DSN)
    try:
        return psycopg.connect(dsn)
    except psycopg.OperationalError as e:
        raise RuntimeError(
            f"could not connect to atlas postgres at {dsn!r}: {e}. "
            "For local dev, run `docker compose up -d` in the repo root "
            "to start a Postgres 18 container. In prod, set ATLAS_DSN to "
            "your Cloud SQL endpoint (typically via the auth proxy)."
        ) from None


_SCHEMA_LOCK_KEY = 0x41544c41530001  # arbitrary fixed pg advisory-lock key for the schema bootstrap
_bootstrapped = False


def bootstrap(conn: Optional[psycopg.Connection] = None, *, force: bool = False) -> None:
    """Apply `schema.sql` against the atlas database. Idempotent — every
    statement in schema.sql is `create ... if not exists` — but the
    apply is wrapped in a transaction-scoped advisory lock so concurrent
    bootstraps don't race on pg_catalog ('tuple concurrently updated').

    A process-level flag suppresses repeated calls so callers can invoke
    bootstrap() defensively on every entrypoint without re-running DDL.
    Pass `force=True` to override (e.g. after editing schema.sql in a
    long-running dev process).

    If `conn` is omitted a throwaway connection is opened, used, and
    closed. If a `conn` is supplied we commit on success (releasing the
    advisory lock) and leave it open.
    """
    global _bootstrapped
    if _bootstrapped and not force:
        return
    from pathlib import Path

    schema_sql = (Path(__file__).resolve().parent / "schema.sql").read_text()
    own_conn = conn is None
    c = conn if conn is not None else connect()
    try:
        with c.cursor() as cur:
            cur.execute("select pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_KEY,))
            cur.execute(schema_sql)
        c.commit()
        _bootstrapped = True
    finally:
        if own_conn:
            c.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="atlas")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_exp = sub.add_parser("export", help="dump a client's schemas + entities + relations to a snapshot file")
    p_exp.add_argument("client")
    p_exp.add_argument("--out", required=True, help="output path (msgpack+gzip)")

    p_imp = sub.add_parser("restore", help="load a snapshot file back into atlas")
    p_imp.add_argument("--from", dest="src", required=True, help="snapshot path (msgpack+gzip)")

    p_web = sub.add_parser("web", help="launch pgweb against the embedded atlas db")
    p_web.add_argument("--port", default="8081", help="pgweb listen port (default 8081)")

    args = parser.parse_args()

    if args.cmd == "web":
        _launch_pgweb(args.port)
        return

    with connect() as conn:
        bootstrap(conn)
        atlas = Atlas(conn)

        if args.cmd == "export":
            _client_module(args.client, "ontology").register_all(atlas)
            counts = export(atlas, args.client, args.out)
            print(f"exported {args.client} → {args.out}:")
            for k, v in counts.items():
                print(f"  {k}: {v}")
        elif args.cmd == "restore":
            counts = restore(atlas, args.src)
            print(f"restored from {args.src}:")
            for k, v in counts.items():
                print(f"  {k}: {v}")


