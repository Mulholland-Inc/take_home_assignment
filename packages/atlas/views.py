"""Auto-generate one SQL view per entity type in a client registry.

Each view projects the jsonb `data` column into typed columns derived
from the pydantic model.

Pydantic annotation → postgres cast:
  int        →  (data->>'f')::int
  float      →  (data->>'f')::numeric
  bool       →  (data->>'f')::boolean
  list[...]  →   data->'f'              (jsonb)
  BaseModel  →   data->'f'              (jsonb)
  anything else (incl. str) → data->>'f' as text

Atlas core stays generic: the view names and column shapes come from the
client's pydantic models, no per-client literals here.
"""

from __future__ import annotations

import inspect
import logging
import typing as _t

from pydantic import BaseModel

from atlas import Atlas

log = logging.getLogger("atlas.views")


def _unwrap(ann):
    """Peel Optional[X] off; return (inner, is_list)."""
    origin = _t.get_origin(ann)
    if origin in (_t.Union, getattr(_t, "UnionType", None)):
        args = [a for a in _t.get_args(ann) if a is not type(None)]
        if len(args) == 1:
            return _unwrap(args[0])
        return ann, False
    if origin in (list, tuple):
        return ann, True
    return ann, False


def _column_sql(name: str, annotation) -> str:
    inner, is_list = _unwrap(annotation)
    if is_list or (inspect.isclass(inner) and issubclass(inner, BaseModel)):
        return f"data->'{name}' as {name}"
    if inner is int:
        return f"(data->>'{name}')::int as {name}"
    if inner is float:
        return f"(data->>'{name}')::numeric as {name}"
    if inner is bool:
        return f"(data->>'{name}')::boolean as {name}"
    return f"data->>'{name}' as {name}"


def view_sql(type_name: str, model: type[BaseModel]) -> str:
    """Render `create or replace view <type> as select ... from entity ...`."""
    view = type_name.lower()
    cols = [_column_sql(f, field.annotation) for f, field in model.model_fields.items()]
    select_list = ",\n       ".join(["id", *cols, "created_at", "updated_at"])
    return (
        f"create or replace view {view} as\n"
        f"select {select_list}\n"
        f"  from entity\n"
        f" where type = '{type_name}';"
    )


def apply(atlas: Atlas, registry: dict[str, type[BaseModel]]) -> list[str]:
    """Drop-and-recreate one view per entity type. Returns the view
    names created, in order. Drops first (with `cascade`) so column-shape
    changes between runs don't trip postgres's view-column-replace check.
    """
    created: list[str] = []
    with atlas.conn.cursor() as cur:
        for type_name in registry:
            cur.execute(f"drop view if exists {type_name.lower()} cascade")
        for type_name, model in registry.items():
            sql = view_sql(type_name, model)
            log.info("view %s", type_name.lower())
            cur.execute(sql)
            created.append(type_name.lower())
    atlas.conn.commit()
    return created
