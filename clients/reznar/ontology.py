"""Reznar's Arcane Oddities — domain ontology.

Define the entity types for Reznar's magic item catalog here.
Each type is a Pydantic BaseModel subclass. Register them all in REGISTRY.

Useful markers from atlas:
  Unique[T]  — deduplication key (atlas won't create a duplicate if this matches)
  Fuzzy[T]   — near-duplicate warning at write time
  Hint("…")  — description surfaced to LLM agents

See clients/stormland/ontology.py for a worked example.
"""

from __future__ import annotations

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Define your entity types here
# ---------------------------------------------------------------------------

# class MagicItem(BaseModel):
#     ...


# ---------------------------------------------------------------------------
# Registry — maps type-name strings to your model classes.
# The atlas CLI and export command rely on this.
# ---------------------------------------------------------------------------

REGISTRY: dict[str, type[BaseModel]] = {
    # "MagicItem": MagicItem,
}


def register_all(atlas) -> None:
    for type_name, model in REGISTRY.items():
        atlas.register(type_name, model)
