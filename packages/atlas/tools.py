"""Shared ontology tools for atlas agents.

Tools (on `OntologyDispatcher`, declared by `ontology_tool_decls`):
  read:  describe, list, get, relations, sql
  write: set, update, delete

`describe(type)` returns the per-field shape (kind, required, unique,
ref_targets, JSON-schema constraints, and Hint text from markers) and
also unlocks writes for that type — `set`/`update` refuse until the
type has been described in the current session. SchemaError responses
include `field_hints` so the LLM can self-correct on validation failure.

Subclass `OntologyDispatcher` and add `do_<name>` methods for non-atlas
tools (corpus reads, web search, file attachments). `read_sources` is
populated by the harness and consumed by `_resolve_sources` on writes.
`ontology_tool_decls(write=False)` hides set/update/delete.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from pydantic import ValidationError

import atlas
from atlas import Atlas, SchemaError
from atlas.client import ClientConfig, describe_fields


log = logging.getLogger("atlas.tools")


def _jsonable(v):
    import datetime
    import decimal
    if v is None or isinstance(v, (bool, int, float, str, list, dict)):
        return v
    if isinstance(v, (decimal.Decimal, datetime.date, datetime.datetime,
                      datetime.time, uuid.UUID)):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    return str(v)


class OntologyDispatcher:
    def __init__(self, atlas_: Atlas, cfg: ClientConfig):
        self.atlas = atlas_
        self.cfg = cfg
        self.counts: dict[str, int] = {}
        self.read_sources: dict[str, uuid.UUID] = {}
        # Types the agent has called describe() on this session. set/
        # update gate on this set so the LLM has seen field-level
        # constraints and Hints before writing.
        self.described_types: set[str] = set()
        # (type, frozen-fields) keys for which set() has surfaced a
        # similar-entity notice this session. A second set() with the
        # same key proceeds to the actual write.
        self._set_shown: set[tuple] = set()
        # Lazy read-only psycopg connection used exclusively by do_sql,
        # so user queries cannot interfere with the write connection's
        # transaction state. Opened on first sql call, closed by close().
        self._sql_conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def close(self) -> None:
        if self._sql_conn is not None:
            try:
                self._sql_conn.close()
            finally:
                self._sql_conn = None

    def _get_sql_conn(self):
        if self._sql_conn is None:
            import os
            import psycopg
            from atlas import _DEFAULT_LOCAL_DSN
            dsn = os.getenv("ATLAS_DSN", _DEFAULT_LOCAL_DSN)
            conn = psycopg.connect(dsn, autocommit=True)
            with conn.cursor() as cur:
                cur.execute(
                    "set session characteristics as transaction read only"
                )
            self._sql_conn = conn
        return self._sql_conn

    def __call__(self, name: str, args: dict) -> tuple[dict, list]:
        handler = getattr(self, f"do_{name}", None)
        if handler is None:
            self.counts["errors"] = self.counts.get("errors", 0) + 1
            return {"error": f"unknown tool {name!r}"}, []
        try:
            result = handler(**args)
        except TypeError as e:
            return {"error": f"bad args to {name}: {e}"}, []
        except Exception as e:
            log.warning("tool.failed name=%s error=%s type=%s",
                        name, str(e), type(e).__name__)
            self.counts["errors"] = self.counts.get("errors", 0) + 1
            return {"error": str(e), "error_type": type(e).__name__}, []
        self.counts[name] = self.counts.get(name, 0) + 1
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], list):
            return result
        return result, []

    def _resolve_sources(self, sources: Optional[list[str]]) -> tuple[list[uuid.UUID], list[str]]:
        if not sources:
            return list(self.read_sources.values()), []
        known_ids = set(self.read_sources.values())
        ids: list[uuid.UUID] = []
        unknown: list[str] = []
        for token in sources:
            sid = self.read_sources.get(token)
            if sid is not None:
                ids.append(sid)
                continue
            try:
                as_uuid = uuid.UUID(token)
            except (ValueError, TypeError):
                unknown.append(token)
                continue
            if as_uuid in known_ids:
                ids.append(as_uuid)
            else:
                unknown.append(token)
        return ids, unknown

    def _top_neighbor(self, type: str, fields: dict,
                      exclude_id: Optional[uuid.UUID] = None) -> Optional[dict]:
        """The single most-similar existing entity of `type` for any
        unique-or-fuzzy string field set in `fields`. No threshold —
        whatever ranks highest by SequenceMatcher wins, even at low
        ratio. Returns the row's id + full data so the model can
        compare and decide whether to update() it or proceed with a
        new set(). Returns None when no candidate rows exist."""
        from difflib import SequenceMatcher
        model_cls = self.cfg.registry.get(type)
        if model_cls is None:
            return None
        # Look across both unique and fuzzy fields: both are identity-
        # bearing for dedup purposes.
        candidate_fields = set(atlas.fuzzy_fields(model_cls)) | set(
            atlas.unique_fields(model_cls))
        if not candidate_fields:
            return None
        best: Optional[tuple[float, uuid.UUID, dict]] = None
        with self.atlas.conn.cursor() as cur:
            for fname in candidate_fields:
                v = fields.get(fname)
                if not isinstance(v, str) or not v.strip():
                    continue
                cur.execute(
                    "select id, data from entity where type = %s and data ? %s",
                    (type, fname),
                )
                for row_id, row_data in cur.fetchall():
                    if exclude_id is not None and row_id == exclude_id:
                        continue
                    existing = (row_data or {}).get(fname)
                    if not isinstance(existing, str):
                        continue
                    ratio = SequenceMatcher(None, v.lower(),
                                            existing.lower()).ratio()
                    if best is None or ratio > best[0]:
                        best = (ratio, row_id, row_data)
        if best is None:
            return None
        ratio, row_id, row_data = best
        return {"id": str(row_id), "type": type,
                "data": row_data, "ratio": round(ratio, 3)}

    def _set_key(self, type: str, fields: dict) -> tuple:
        """Hashable key identifying a (type, fields) pair, so two calls
        to set() with the same payload can be matched across turns."""
        import json
        return (type, json.dumps(fields, sort_keys=True, default=str))

    def _field_hints_for(self, type: str, ve: ValidationError) -> dict:
        """For each field path in `ve.errors()`, look up the model's
        Hint + constraints so the LLM can self-correct."""
        model_cls = self.cfg.registry.get(type)
        if model_cls is None:
            return {}
        by_name = {f["name"]: f for f in describe_fields(model_cls)}
        out: dict[str, dict] = {}
        for err in ve.errors():
            loc = err.get("loc") or ()
            if not loc:
                continue
            field = loc[0]
            if not isinstance(field, str) or field in out:
                continue
            info = by_name.get(field)
            if info is None:
                continue
            entry: dict = {"msg": err.get("msg")}
            if info.get("hint"):
                entry["hint"] = info["hint"]
            if info.get("constraints"):
                entry["constraints"] = info["constraints"]
            if info.get("kind"):
                entry["kind"] = info["kind"]
            out[field] = entry
        return out

    def _wrap_schema_error(self, type: str, e: SchemaError) -> dict:
        out = {"error": str(e), "error_type": "SchemaError"}
        ve = e.__cause__
        if isinstance(ve, ValidationError):
            hints = self._field_hints_for(type, ve)
            if hints:
                out["field_hints"] = hints
        return out

    def do_describe(self, type: str) -> dict:
        if type not in self.cfg.registry:
            return {"error": f"unknown type {type!r}"}
        model_cls = self.cfg.registry[type]
        self.described_types.add(type)
        import inspect as _inspect
        doc = _inspect.cleandoc(model_cls.__doc__ or "")
        return {"type": type, "doc": doc, "fields": describe_fields(model_cls)}

    def do_list(self, type: str, where: Optional[dict] = None) -> dict:
        if type not in self.cfg.registry:
            return {"error": f"unknown type {type!r}"}
        rows = self.atlas.query(type, where=where or None)
        return {"matches": [
            {"id": str(r["id"]), "type": type, "data": r["data"]}
            for r in rows[:200]
        ], "count": len(rows)}

    def do_get(self, id: str) -> dict:
        try:
            eid = uuid.UUID(id)
        except (ValueError, TypeError):
            return {"error": f"not a uuid: {id!r}"}
        with self.atlas.conn.cursor() as cur:
            cur.execute(
                "select id, type, data, created_at, updated_at "
                "from entity where id = %s", (eid,))
            row = cur.fetchone()
            if not row:
                return {"error": f"no entity {id}"}
            rid, rtype, rdata, c_at, u_at = row
            cur.execute(
                "select s.id, s.kind, s.uri, s.metadata "
                "from entity_source s "
                "join entity_source_link l on l.source_id = s.id "
                "where l.entity_id = %s", (eid,))
            sources = [
                {"id": str(sid), "kind": kind, "uri": uri,
                 "filename": (md or {}).get("filename")}
                for sid, kind, uri, md in cur.fetchall()
            ]
            cur.execute(
                "select op, reason, at from entity_audit "
                "where entity_id = %s order by at desc limit 20", (eid,))
            audit = [{"op": op, "reason": reason, "at": at.isoformat()}
                     for op, reason, at in cur.fetchall()]
        return {
            "id": str(rid), "type": rtype, "data": rdata,
            "created_at": c_at.isoformat() if c_at else None,
            "updated_at": u_at.isoformat() if u_at else None,
            "sources": sources, "audit": audit,
        }

    def do_relations(self, id: str, depth: int = 1) -> dict:
        try:
            root = uuid.UUID(id)
        except (ValueError, TypeError):
            return {"error": f"not a uuid: {id!r}"}
        depth = max(1, min(int(depth), 5))
        nodes: dict[uuid.UUID, dict] = {}
        edges: list[dict] = []
        seen_edges: set[tuple[str, str, str, str]] = set()
        with self.atlas.conn.cursor() as cur:
            cur.execute("select id, type, data from entity where id = %s", (root,))
            row = cur.fetchone()
            if not row:
                return {"error": f"no entity {id}"}
            nodes[root] = {"id": str(row[0]), "type": row[1], "data": row[2]}
            frontier = {root}
            for _ in range(depth):
                next_frontier: set[uuid.UUID] = set()
                for eid in frontier:
                    data = nodes[eid]["data"] or {}
                    for k, v in data.items():
                        for c in (v if isinstance(v, list) else [v]):
                            if not isinstance(c, str):
                                continue
                            try:
                                ref_id = uuid.UUID(c)
                            except (ValueError, TypeError):
                                continue
                            edge_key = (str(eid), str(ref_id), k, "forward")
                            if edge_key in seen_edges:
                                continue
                            cur.execute(
                                "select id, type, data from entity where id = %s",
                                (ref_id,))
                            r = cur.fetchone()
                            if not r:
                                continue
                            if ref_id not in nodes:
                                nodes[ref_id] = {"id": str(r[0]), "type": r[1], "data": r[2]}
                                next_frontier.add(ref_id)
                            seen_edges.add(edge_key)
                            edges.append({"src": str(eid), "dst": str(ref_id),
                                          "via_field": k, "direction": "forward"})
                    cur.execute(
                        "select id, type, data from entity "
                        "where data::text like %s and id <> %s",
                        (f"%{eid}%", eid))
                    for rid, rtype, rdata in cur.fetchall():
                        via = next(
                            (k for k, v in (rdata or {}).items()
                             if (isinstance(v, str) and v == str(eid))
                             or (isinstance(v, list) and str(eid) in v)),
                            None,
                        )
                        if via is None:
                            continue
                        edge_key = (str(rid), str(eid), via, "reverse")
                        if edge_key in seen_edges:
                            continue
                        if rid not in nodes:
                            nodes[rid] = {"id": str(rid), "type": rtype, "data": rdata}
                            next_frontier.add(rid)
                        seen_edges.add(edge_key)
                        edges.append({"src": str(rid), "dst": str(eid),
                                      "via_field": via, "direction": "reverse"})
                if not next_frontier:
                    break
                frontier = next_frontier
        return {"root": str(root), "depth": depth,
                "nodes": list(nodes.values()), "edges": edges}

    def do_sql(self, query: str, max_rows: int = 50) -> dict:
        if len(query) > 8000:
            return {"error": "query too long (max 8000 chars)"}
        conn = self._get_sql_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(query)
                cols = ([d.name for d in cur.description]
                        if cur.description else [])
                raw = cur.fetchmany(max_rows + 1) if cols else []
            truncated = len(raw) > max_rows
            rows = [
                {cols[i]: _jsonable(r[i]) for i in range(len(cols))}
                for r in raw[:max_rows]
            ]
            return {"columns": cols, "rows": rows,
                    "row_count": len(rows), "truncated": truncated}
        except Exception as e:
            # Read-only autocommit connection: a failed statement leaves
            # the connection in a clean state, no rollback dance needed.
            return {"error": str(e)[:600],
                    "error_type": type(e).__name__}

    def do_set(self, type: str, fields: dict, reason: str,
                  sources: Optional[list[str]] = None) -> dict:
        if type not in self.cfg.registry:
            return {"error": f"unknown type {type!r}"}
        if type not in self.described_types:
            return {"error": f"describe(type={type!r}) first — read the "
                             "field types and constraints before writing"}
        src_ids, unknown = self._resolve_sources(sources)
        if not src_ids:
            return {"error": "no sources available; register a source or pass `sources`"}
        # Pre-write check: on first call with this (type, fields) payload,
        # if any existing entity of this type already has a value in a
        # unique/fuzzy field, surface the single closest match and skip
        # the write. The model can then either update() the match (if
        # it's the same logical entity) or call set() again with the
        # same fields (signalling 'I've seen the neighbor, still want a
        # new entity'). The check re-queries the DB on every call so it
        # stays correct under concurrent agents.
        key = self._set_key(type, fields)
        if key not in self._set_shown:
            neighbor = self._top_neighbor(type, fields)
            if neighbor is not None:
                self._set_shown.add(key)
                return {
                    "pending": True,
                    "similar": neighbor,
                    "notice": (
                        f"a {type} entity already exists that resembles "
                        f"this one (id={neighbor['id']}). If it's the "
                        f"same entity, call update on that id instead. "
                        f"If it's genuinely different, call set again "
                        f"with the same fields to confirm."
                    ),
                }
        try:
            eid = self.atlas.upsert(type, fields, reason=reason, sources=src_ids)
        except SchemaError as e:
            return self._wrap_schema_error(type, e)
        # Confirmation consumed — drop the key so a future set() with the
        # same payload re-checks against whatever the DB looks like then.
        self._set_shown.discard(key)
        out = {"id": str(eid)}
        if unknown:
            out["unknown_sources"] = unknown
        return out

    def do_update(self, id: str, fields: dict, reason: str,
                  sources: Optional[list[str]] = None) -> dict:
        try:
            eid = uuid.UUID(id)
        except (ValueError, TypeError):
            return {"error": f"not a uuid: {id!r}"}
        with self.atlas.conn.cursor() as cur:
            cur.execute("select type from entity where id = %s", (eid,))
            row = cur.fetchone()
        if row is None:
            return {"error": f"no entity {id}"}
        entity_type = row[0]
        if entity_type not in self.described_types:
            return {"error": f"describe(type={entity_type!r}) first — read "
                             "the field types and constraints before writing"}
        src_ids, unknown = self._resolve_sources(sources)
        try:
            self.atlas.correct(eid, fields, reason=reason, sources=src_ids)
        except SchemaError as e:
            return self._wrap_schema_error(entity_type, e)
        except Exception as e:
            return {"error": str(e), "error_type": type(e).__name__}
        out: dict = {"ok": True}
        if unknown:
            out["unknown_sources"] = unknown
        return out

    def do_delete(self, id: str, reason: str) -> dict:
        eid = uuid.UUID(id)
        with self.atlas.conn.cursor() as cur:
            cur.execute(
                "insert into entity_audit (op, entity_id, reason) values (%s, %s, %s)",
                ("delete", eid, reason),
            )
            cur.execute("delete from entity where id = %s", (eid,))
            n = cur.rowcount
        return {"deleted": n}


def _obj(props: dict, required: Optional[list[str]] = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


def ontology_tool_decls(*, write: bool = True):
    from google.genai import types as gt

    decls = [
        gt.FunctionDeclaration(
            name="describe",
            description="Return the field shape of an ontology type: python_type, required, unique, fuzzy, ref_targets, schema constraints (minimum, maximum, pattern, enum), and hint text. Must be called for a type before set or update is allowed on that type.",
            parameters_json_schema=_obj({"type": {"type": "string"}}, ["type"]),
        ),
        gt.FunctionDeclaration(
            name="list",
            description="List entities of this ontology type, optionally filtered by exact-match fields in `where` (e.g. {\"state\": \"OK\"}).",
            parameters_json_schema=_obj({
                "type": {"type": "string"},
                "where": {"type": "object"},
            }, ["type"]),
        ),
        gt.FunctionDeclaration(
            name="get",
            description="Return one ontology entity by id: its type, data, linked source documents, and audit history.",
            parameters_json_schema=_obj({"id": {"type": "string"}}, ["id"]),
        ),
        gt.FunctionDeclaration(
            name="relations",
            description="Walk the ontology graph from `id` up to `depth` hops (default 1, max 5). Returns {root, depth, nodes, edges} where each edge has {src, dst, via_field, direction: 'forward' or 'reverse'}.",
            parameters_json_schema=_obj({
                "id": {"type": "string"},
                "depth": {"type": "integer"},
            }, ["id"]),
        ),
        gt.FunctionDeclaration(
            name="sql",
            description="Run a read-only SQL SELECT against the ontology database. Anything other than SELECT is rolled back. Returns {columns, rows, row_count, truncated}.",
            parameters_json_schema=_obj({
                "query": {"type": "string"},
                "max_rows": {"type": "integer"},
            }, ["query"]),
        ),
    ]
    if write:
        decls.extend([
            gt.FunctionDeclaration(
                name="set",
                description=(
                    "Insert or merge an ontology entity of this type. If an existing entity matches "
                    "on any of its unique fields (see describe()), the new fields are merged into "
                    "that record; otherwise a new record is inserted. Returns the entity id. "
                    "First call: if any existing entity of this type already has a value in a "
                    "unique/fuzzy field, set() does NOT write — it returns {pending: true, "
                    "similar: <closest existing entity>, notice: ...}. Compare against the "
                    "returned `similar` entity: if it's the same thing, call update on its id "
                    "instead; if it's genuinely different, call set again with the same fields "
                    "to write a new record."
                ),
                parameters_json_schema=_obj({
                    "type": {"type": "string"},
                    "fields": {"type": "object"},
                    "reason": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "string"}},
                }, ["type", "fields", "reason"]),
            ),
            gt.FunctionDeclaration(
                name="update",
                description="Overwrite specific fields on an existing ontology entity by id.",
                parameters_json_schema=_obj({
                    "id": {"type": "string"},
                    "fields": {"type": "object"},
                    "reason": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "string"}},
                }, ["id", "fields", "reason"]),
            ),
            gt.FunctionDeclaration(
                name="delete",
                description="Permanently delete an ontology entity by id. Cascades to its relations and audit rows.",
                parameters_json_schema=_obj({
                    "id": {"type": "string"},
                    "reason": {"type": "string"},
                }, ["id", "reason"]),
            ),
        ])
    return decls


__all__ = ["OntologyDispatcher", "ontology_tool_decls"]
