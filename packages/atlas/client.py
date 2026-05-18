"""Client ontology loader + system-prompt rendering helpers.

A "client" here is an installed Python package (e.g. `stormland`, `reznar`)
that ships an `ontology` submodule with:
  - REGISTRY: dict[str, type[BaseModel]]
  - CLIENT_CONTEXT: str (free-form description for the agent prompt)
  - register_all(atlas) is optional — ClientConfig.register_all() does it
    based on REGISTRY alone.

The render helpers in here produce the human-readable type block that
gets stitched into agent system prompts. Both monorail (write agent)
and orient (read agent) share the same renderer so their ontology
descriptions stay consistent.
"""

from __future__ import annotations

import importlib
import inspect
import typing as _t
from dataclasses import dataclass

from pydantic import BaseModel

from atlas import Atlas
from atlas.markers import Hint


@dataclass
class ClientConfig:
    name: str
    context: str
    registry: dict[str, type[BaseModel]]

    def register_all(self, atlas: Atlas) -> None:
        for type_name, model in self.registry.items():
            atlas.register(type_name, model)


def load_client(client: str) -> ClientConfig:
    """Import `<client>.ontology` and pull REGISTRY + CLIENT_CONTEXT out."""
    mod = importlib.import_module(f"{client}.ontology")
    return ClientConfig(
        name=client,
        context=getattr(mod, "CLIENT_CONTEXT", ""),
        registry=mod.REGISTRY,
    )


def _collect_hints(node) -> list[str]:
    """Walk an annotation / metadata subtree and collect every Hint.text.
    Descends through Annotated (__metadata__ + __origin__) and Union /
    Optional / list[…] (typing.get_args). Pydantic flattens markers
    onto `field.metadata` for required fields but NOT through Optional;
    this walker handles both."""
    out: list[str] = []
    if isinstance(node, Hint):
        out.append(node.text)
        return out
    meta = getattr(node, "__metadata__", None)
    if meta is not None:
        for m in meta:
            out.extend(_collect_hints(m))
        origin = getattr(node, "__origin__", None)
        if origin is not None:
            out.extend(_collect_hints(origin))
        return out
    origin = _t.get_origin(node)
    if origin is not None:
        for a in _t.get_args(node):
            if a is type(None):
                continue
            out.extend(_collect_hints(a))
    return out


def _field_hints(field) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    candidates: list[str] = []
    if field.description:
        candidates.append(field.description)
    for m in field.metadata:
        candidates.extend(_collect_hints(m))
    candidates.extend(_collect_hints(field.annotation))
    for s in candidates:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def render_types(registry: dict[str, type[BaseModel]]) -> str:
    """One line per entity type: `<Name> — <first paragraph of docstring>`.
    Field-level shape (kind, constraints, hints) is intentionally omitted —
    the agent calls describe(type) to see that."""
    blocks: list[str] = []
    for name, cls in registry.items():
        doc = inspect.cleandoc(cls.__doc__ or "").split("\n\n", 1)[0].replace("\n", " ")
        blocks.append(f"{name} — {doc}")
    return "\n".join(blocks)


def _python_type_name(ann) -> str:
    """Short, LLM-friendly Python type label for a field's core type.
    Walks Optional / Union / Annotated / list[…]."""
    origin = _t.get_origin(ann)
    if origin is _t.Union:
        non_null = [a for a in _t.get_args(ann) if a is not type(None)]
        if len(non_null) == 1:
            return _python_type_name(non_null[0])
        return " | ".join(_python_type_name(a) for a in non_null)
    if hasattr(ann, "__metadata__"):
        return _python_type_name(ann.__origin__)
    if origin in (list, tuple, set):
        args = _t.get_args(ann)
        inner = _python_type_name(args[0]) if args else "Any"
        return f"{origin.__name__}[{inner}]"
    return getattr(ann, "__name__", str(ann))


def describe_fields(model: type[BaseModel]) -> list[dict]:
    """Per-field shape: pydantic's JSON schema (constraints, types,
    anyOf for Optional, etc.) plus the Python type label, marker Hint,
    and atlas-side flags (unique/fuzzy/ref_targets) that don't live in
    JSON schema."""
    import atlas as _atlas
    schema = model.model_json_schema()
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    unique = set(_atlas.unique_fields(model))
    fuzzy = set(_atlas.fuzzy_fields(model))
    refs = dict(_atlas.ref_fields(model))

    out: list[dict] = []
    for name, field in model.model_fields.items():
        info: dict = {
            "name": name,
            "python_type": _python_type_name(field.annotation),
            "required": name in required,
            "schema": props.get(name, {}),
        }
        hints = _field_hints(field)
        if hints:
            info["hint"] = " | ".join(hints)
        if name in unique:
            info["unique"] = True
        if name in fuzzy:
            info["fuzzy"] = True
        if name in refs:
            info["ref_targets"] = list(refs[name])
        out.append(info)
    return out


__all__ = ["ClientConfig", "load_client", "render_types", "describe_fields"]
