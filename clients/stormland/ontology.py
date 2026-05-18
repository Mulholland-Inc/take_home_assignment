"""StormlandHoldings domain ontology — the canonical, atlas-side source of truth.

Pydantic models are the t-box; their class names are the entity-type
identifiers used everywhere. Domain primitives (USAddress, USState,
USZip, Money, SqFt, Year) live in `atlas` so other clients can reuse
them. A failed validation surfaces as a ValidationError the agent loop
reads and self-corrects from.

Relationships are declared as `Optional[Ref[T]]` fields on the entity
that needs them — e.g. `tenant_id: Optional[Ref[Tenant]]` on Lease.
Refs are optional so entities can land as soon as they're seen and
get wired up via `update()` once the counterparty is identified.
Atlas verifies at write time that any non-null ref points at an
existing entity of an allowed type. Discovered facts the LLM finds
that aren't part of the entity schema still go through the open-vocab
`link()` tool (e.g. `has_ceo`, `receives_notice_at`).

Class docstrings are written as instructions to a downstream LLM
extractor: they tell the model what counts as an entity of this type
and what does NOT.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from atlas import (
    Fuzzy, Hint, Ref, Unique,
    EIN, Email, Money, Percentage, PhoneE164, SqFt,
    USAddress, USState, USZip, Year,
)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Property(_Base):
    """A leased space — a whole commercial parcel or a specific bay/
    suite within one — identified by its street address. For a multi-
    tenant parcel addressed as a range (e.g. '5400-5408 S. 122nd E.
    Ave'), create one Property per occupied bay using its specific
    sub-address ('5400 S. 122nd E. Ave', '5404 S. 122nd E. Ave', etc.).
    If a single lease covers the entire parcel with no subdivision,
    one Property with the primary address is enough.

    NOT a Property: abstract document labels like 'the Premises', 'the
    Building', 'the Demised Premises'. Always resolve those to the
    concrete address the document is about."""
    address:    Unique[USAddress] = Field(min_length=1)
    city:       Optional[str]     = None
    state:      Optional[USState] = None
    zip:        Optional[USZip]   = None
    total_sqft: Optional[SqFt]    = None
    year_built: Optional[Year]    = None


class Tenant(_Base):
    """A specific company or person named as the lessee on a Lease — the
    party leasing space. NOT a Tenant: the literal role-name words
    'Tenant', 'Lessee', or 'the Tenant' used as labels in the document;
    those refer to whichever specific named party is the lessee.

    If the same legal entity is also a Landlord on another lease,
    create both — they are distinct role-typed entities."""
    name:        Annotated[Unique[str], Fuzzy[str]] = Field(min_length=1)
    tenant_code: Optional[Unique[str]]              = None
    ein:         Optional[Unique[EIN]]              = None
    industry:    Optional[str]                      = None


class Landlord(_Base):
    """A specific company or person named as the lessor / landlord on a
    Lease — the party granting the lease. NOT a Landlord: the literal
    role-name words 'Landlord', 'Lessor', or 'the Landlord' used as
    labels in the document.

    If the same legal entity is also a Tenant or Investor elsewhere,
    create separate role-typed entities for each role — same legal
    entity, different rows."""
    name:               Annotated[Unique[str], Fuzzy[str]] = Field(min_length=1)
    entity_type:        Optional[Annotated[str, Hint("legal form: LLC, LP, Inc., Corp., Trust, etc.")]] = None
    state_of_formation: Optional[USState]                  = None
    ein:                Optional[Unique[EIN]]              = None


class Investor(_Base):
    """A person or fund holding equity in a Project. Distinct from
    Landlord: an Investor owns equity, a Landlord is the lessor party
    on a lease. The same legal entity can play both roles, but extract
    them as separate role-typed entities."""
    name:        Annotated[Unique[str], Fuzzy[str]] = Field(min_length=1)
    investor_id: Optional[Unique[str]]              = None
    ein:         Optional[Unique[EIN]]              = None
    email:       Optional[Email]                    = None
    phone:       Optional[PhoneE164]                = None


class Project(_Base):
    """One acquisition or deal — typically a single property or a small
    cluster of related properties bought together. StormlandHoldings manages
    multiple active Projects across several markets."""
    name:         Annotated[Unique[str], Fuzzy[str]] = Field(min_length=1)
    market:       Optional[Annotated[str, Hint("market or metro, e.g. 'NYC', 'Chicago', 'Austin'")]] = None
    property_ids: list[Ref[Property]]                = Field(default_factory=list, description="Properties this Project owns / includes")


class Lease(_Base):
    """A rental agreement. References the parties (Tenant + Landlord)
    and the leased space (a Property) directly via ref fields. Carries
    start/end dates, base rent, and deposit.

    Per-period rent steps live in separate Charge rows linked back via
    Charge.lease_id; that lets the agent split the original schedule
    from amendment-driven revisions cleanly."""
    tenant_id:         Optional[Ref[Tenant]]   = Field(default=None, description="the lessee")
    landlord_id:       Optional[Ref[Landlord]] = Field(default=None, description="the lessor / landlord granting the lease")
    premises_id:       Optional[Ref[Property]] = Field(default=None, description="the leased Property (whole parcel or specific bay/suite — Units are not modelled)")
    start_date:        date
    base_rent_monthly: Money
    lease_id:          Optional[Unique[str]] = Field(default=None, min_length=1)
    end_date:          Optional[date]        = None
    term_months:       Optional[PositiveInt] = None
    rent_per_sqft:     Optional[Money]       = None
    deposit:           Optional[Money]       = None


class LeaseAmendment(_Base):
    """A formal amendment, modification, or extension to an existing
    Lease. Points at the Lease it modifies via `lease_id`."""
    lease_id:         Optional[Ref[Lease]]     = Field(default=None, description="the Lease being amended")
    effective_date:   date
    amendment_number: Optional[PositiveInt]    = None
    change_summary:   Optional[Annotated[str, Hint("one-sentence summary of what changed")]] = None


class Charge(_Base):
    """A billable line item under a Lease — base rent for a specific
    period, CAM, taxes, escalation steps, late fees, etc. The lease's
    full payment schedule is typically a series of Charges with
    consecutive period_start / period_end dates."""
    lease_id:     Optional[Ref[Lease]]                             = Field(default=None, description="the Lease this charge is under")
    amount:       Money
    kind:         Annotated[str, Hint("'base rent', 'CAM', 'tax', 'escalation', 'late fee', etc.")]
    period_start: Optional[date]                                   = None
    period_end:   Optional[date]                                   = None


class Investment(_Base):
    """An Investor's standing position in a Project — capital committed,
    capital contributed, ownership percentage."""
    investor_id:         Optional[Ref[Investor]] = Field(default=None, description="the equity holder")
    project_id:          Optional[Ref[Project]]  = Field(default=None, description="the deal they invested in")
    capital_committed:   Optional[Money] = None
    capital_contributed: Optional[Money] = None
    ownership_pct:       Optional[Percentage] = None


REGISTRY: dict[str, type[BaseModel]] = {
    cls.__name__: cls
    for cls in _Base.__subclasses__()
}


def register_all(atlas) -> None:
    for type_name, model in REGISTRY.items():
        atlas.register(type_name, model)
