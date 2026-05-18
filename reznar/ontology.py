"""Reznar's Arcane Oddities — domain ontology.

Define the entity types for Reznar's magic item catalog here as
Pydantic models. See ../stormland/ontology.py for a worked example.
"""

from __future__ import annotations

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Define your entity types here
# ---------------------------------------------------------------------------

# class MagicItem(BaseModel):
#     ...


# ---------------------------------------------------------------------------
# Registry — maps type-name strings to your model classes, so the rest of
# your pipeline can discover them generically.
# ---------------------------------------------------------------------------

REGISTRY: dict[str, type[BaseModel]] = {
    # "MagicItem": MagicItem,
}
