"""StormlandHoldings domain ontology — worked example.

Pydantic models define the entity types. Optional[...] marks fields
that may be absent at extraction time. Cross-entity references are
plain UUIDs (e.g. `tenant_id` on a Lease points at a Tenant row your
pipeline inserted).

Class docstrings are written as instructions to a downstream LLM
extractor: they tell the model what counts as an entity of this type
and what does NOT.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PositiveInt


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Property(_Base):
    """A leased space — a whole commercial parcel or a specific bay/
    suite within one — identified by its street address. For a multi-
    tenant parcel addressed as a range (e.g. '5400-5408 S. 122nd E.
    Ave'), create one Property per occupied bay using its specific
    sub-address.

    NOT a Property: abstract document labels like 'the Premises', 'the
    Building', 'the Demised Premises'. Always resolve those to the
    concrete address the document is about."""
    address:    str            = Field(min_length=1)
    city:       Optional[str]  = None
    state:      Optional[str]  = None
    zip:        Optional[str]  = None
    total_sqft: Optional[float] = Field(default=None, gt=0)
    year_built: Optional[int]  = Field(default=None, ge=1800, le=2100)


class Tenant(_Base):
    """A specific company or person named as the lessee on a Lease — the
    party leasing space. NOT a Tenant: the literal role-name words
    'Tenant', 'Lessee', or 'the Tenant' used as labels in the document."""
    name:        str          = Field(min_length=1)
    tenant_code: Optional[str] = None
    ein:         Optional[str] = None
    industry:    Optional[str] = None


class Landlord(_Base):
    """A specific company or person named as the lessor / landlord on a
    Lease — the party granting the lease."""
    name:               str           = Field(min_length=1)
    entity_type:        Optional[str] = None  # legal form: LLC, LP, Inc., Corp., Trust, ...
    state_of_formation: Optional[str] = None
    ein:                Optional[str] = None


class Lease(_Base):
    """A rental agreement. References the parties (Tenant + Landlord)
    and the leased space (a Property) via UUID fields. Carries start/
    end dates, base rent, and deposit."""
    tenant_id:         Optional[UUID]        = None
    landlord_id:       Optional[UUID]        = None
    premises_id:       Optional[UUID]        = None
    start_date:        date
    base_rent_monthly: Decimal               = Field(ge=0)
    lease_id:          Optional[str]         = Field(default=None, min_length=1)
    end_date:          Optional[date]        = None
    term_months:       Optional[PositiveInt] = None
    deposit:           Optional[Decimal]     = Field(default=None, ge=0)


REGISTRY: dict[str, type[BaseModel]] = {
    "Property": Property,
    "Tenant":   Tenant,
    "Landlord": Landlord,
    "Lease":    Lease,
}
