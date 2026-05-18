"""Shared atlas markers and domain types.

Markers (Unique, Fuzzy, Hint) tag pydantic fields for atlas to read via
reflection: Unique fields are write-time dedup keys; Fuzzy fields opt
into near-duplicate warnings at write time; Hint carries a free text
description used in agent prompts.

Domain types are Annotated aliases that combine a BeforeValidator
(canonicalising the value) with a Hint (documenting the canonical
form). US-shaped (USState, USZip, USAddress) and generic real-estate /
finance scalars (Money, SqFt, Year).

Client ontologies import everything from here via `from atlas import
...` (atlas.py re-exports) so the markers, validators, and atlas core
never form a circular import.
"""

from __future__ import annotations

import re as _re
import uuid as _uuid
from decimal import Decimal
from typing import Annotated

import pycountry
import usaddress
from pydantic import BeforeValidator, Field
from pyzipcode import ZipCodeDatabase

_UNIQUE_MARKER = "_atlas_unique"
_FUZZY_MARKER = "_atlas_fuzzy"


class Unique:
    def __class_getitem__(cls, item):
        return Annotated[item, _UNIQUE_MARKER]


class Fuzzy:
    def __class_getitem__(cls, item):
        return Annotated[item, _FUZZY_MARKER]


class _RefMarker:
    """Marker dropped into `Annotated[uuid.UUID, _RefMarker((Tenant,))]`
    by `Ref[Tenant]`. Atlas inspects the field's metadata to find it,
    reads `target_names`, and validates at write time that the supplied
    uuid points at an existing entity whose `type` is one of the
    listed Pydantic class names."""
    __slots__ = ("target_names",)

    def __init__(self, target_names: tuple[str, ...]):
        self.target_names = target_names

    def __repr__(self) -> str:
        return f"Ref({'|'.join(self.target_names)})"


class Ref:
    """A foreign reference to another entity by uuid. The target type(s)
    are captured at declaration time:

        author_id:  Ref[Tenant]              # required, must reference a Tenant
        premises_id: Ref[Unit, Property]     # may reference either

    Atlas validates at write time that the supplied uuid exists and the
    target row's `type` is one of the allowed targets. Use Optional
    around it for nullable refs:

        guarantor_id: Optional[Ref[Guarantor]] = None
    """
    def __class_getitem__(cls, item):
        if isinstance(item, tuple):
            targets = item
        else:
            targets = (item,)
        names = tuple(t.__name__ for t in targets)
        return Annotated[_uuid.UUID, _RefMarker(names)]


class Hint:
    """Human description for a domain primitive, carried as Annotated
    metadata. Atlas reads it via reflection to render the extraction
    agent's prompt so the model knows what `Money`, `USState`, … mean
    before it ever sees a validator error.

    Use:  Money = Annotated[Decimal, Field(ge=0), Hint("decimal ≥ 0")]
    """
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text

    def __repr__(self) -> str:
        return f"Hint({self.text!r})"


_zcdb = ZipCodeDatabase()


_DIRECTIONS = {
    "N": "N", "NORTH": "N",
    "S": "S", "SOUTH": "S",
    "E": "E", "EAST":  "E",
    "W": "W", "WEST":  "W",
    "NE": "NE", "NORTHEAST": "NE",
    "NW": "NW", "NORTHWEST": "NW",
    "SE": "SE", "SOUTHEAST": "SE",
    "SW": "SW", "SOUTHWEST": "SW",
}

_SUFFIXES = {
    "AVE": "AVE", "AVENUE": "AVE", "AV": "AVE",
    "ST":  "ST",  "STREET": "ST",
    "RD":  "RD",  "ROAD":   "RD",
    "BLVD":"BLVD","BOULEVARD":"BLVD",
    "DR":  "DR",  "DRIVE":  "DR",
    "LN":  "LN",  "LANE":   "LN",
    "CT":  "CT",  "COURT":  "CT",
    "PL":  "PL",  "PLACE":  "PL",
    "HWY": "HWY", "HIGHWAY":"HWY",
    "PKWY":"PKWY","PARKWAY":"PKWY",
    "WAY": "WAY",
    "CIR": "CIR", "CIRCLE": "CIR",
    "TER": "TER", "TERRACE":"TER",
    "TRL": "TRL", "TRAIL":  "TRL",
}


def _abbr(value: str | None, table: dict[str, str]) -> str:
    if not value:
        return ""
    key = value.strip().rstrip(".").upper()
    return table.get(key, key)


def _check_us_state(v):
    if v is None:
        return v
    s = str(v).strip().upper()
    if len(s) != 2 or not pycountry.subdivisions.get(code=f"US-{s}"):
        raise ValueError(
            f"expected USPS 2-letter US state code (e.g. 'OK'), got {v!r}"
        )
    return s


def _check_us_zip(v):
    if v is None:
        return v
    s = str(v).strip()
    if s.split("-", 1)[0] not in _zcdb:
        raise ValueError(f"not a US ZIP: {v!r}")
    return s


def _canonical_us_address(v):
    if v is None:
        return v
    s = str(v).strip()
    try:
        parts, kind = usaddress.tag(s)
    except usaddress.RepeatedLabelError as e:
        raise ValueError(f"address could not be parsed: {v!r} ({e})")
    if kind != "Street Address":
        raise ValueError(f"not a street address: {v!r} (parsed as {kind!r})")

    # usaddress sometimes folds a trailing directional into StreetName
    # ("122nd East" rather than splitting "East" out). Detect that.
    name = parts.get("StreetName", "")
    post = parts.get("StreetNamePostDirectional", "")
    if name and not post:
        last = name.rsplit(None, 1)[-1] if " " in name else ""
        if last and last.strip(".").upper() in _DIRECTIONS:
            post = last
            name = name.rsplit(None, 1)[0]

    # Canonical order: <num> <preDir> <streetName> <postDir> <suffix>.
    # Local South-Central US usage writes the post-direction before the
    # suffix ("100 S. 122nd E. Ave."), so follow the document convention
    # rather than strict USPS Pub 28 ordering.
    pieces = [
        parts.get("AddressNumber", "").strip(),
        _abbr(parts.get("StreetNamePreDirectional"), _DIRECTIONS),
        name.strip(),
        _abbr(post, _DIRECTIONS),
        _abbr(parts.get("StreetNamePostType"), _SUFFIXES),
    ]
    canonical = " ".join(p for p in pieces if p).upper()
    occ = parts.get("OccupancyIdentifier") or ""
    if occ:
        canonical += f" #{occ.strip().upper()}"
    return canonical


USState   = Annotated[str,     BeforeValidator(_check_us_state),       Hint("USPS 2-letter US state code, uppercase (e.g. 'OK'). Full names are rejected.")]
USZip     = Annotated[str,     BeforeValidator(_check_us_zip),         Hint("US ZIP (5 or ZIP+4)")]
USAddress = Annotated[str,     BeforeValidator(_canonical_us_address), Hint("US street address; normalized to '<number> <preDir> <name> <suffix> <postDir>' uppercase. 'S./South' both → 'S', 'Avenue/Ave./AV' → 'AVE'.")]
Money     = Annotated[Decimal, Field(ge=0),                            Hint("decimal number ≥ 0")]
SqFt      = Annotated[float,   Field(gt=0),                            Hint("square feet (> 0)")]
Year      = Annotated[int,     Field(ge=1800, le=2100),                Hint("year in [1800, 2100]")]


# ---- Generic numeric markers --------------------------------------------

Percentage = Annotated[Decimal, Field(ge=0, le=100), Hint("percentage in [0, 100]")]
Ratio      = Annotated[float,   Field(ge=0, le=1),  Hint("ratio in [0, 1]")]
Count      = Annotated[int,     Field(ge=0),        Hint("non-negative integer count (≥ 0)")]


# ---- Contact markers -----------------------------------------------------

_EMAIL_RE = _re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", _re.IGNORECASE)


def _check_email(v):
    if v is None:
        return v
    s = str(v).strip()
    if not _EMAIL_RE.match(s):
        raise ValueError(f"not an email address: {v!r}")
    return s.lower()


_E164_RE = _re.compile(r"^\+[1-9]\d{6,14}$")


def _check_phone_e164(v):
    """Canonicalise to E.164 (+<country><digits>). Accepts free-form input
    with spaces, dashes, parens; assumes US (+1) when there is no leading
    + and the digit count is 10 or 11 (with leading 1)."""
    if v is None:
        return v
    raw = str(v).strip()
    if not raw:
        raise ValueError("empty phone number")
    plus = raw.startswith("+")
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not plus:
        if len(digits) == 10:
            digits = "1" + digits
        elif len(digits) == 11 and digits.startswith("1"):
            pass
        else:
            raise ValueError(
                f"not parseable as phone: {v!r} (expected +<country><number> "
                "or a 10/11-digit US number)"
            )
    canonical = "+" + digits
    if not _E164_RE.match(canonical):
        raise ValueError(f"not E.164 after normalisation: {v!r} -> {canonical}")
    return canonical


_URL_RE = _re.compile(r"^https?://[^\s/?#]+(?:[/?#][^\s]*)?$", _re.IGNORECASE)


def _check_url(v):
    if v is None:
        return v
    s = str(v).strip()
    if not _URL_RE.match(s):
        raise ValueError(f"not an http(s) URL: {v!r}")
    return s


Email     = Annotated[str, BeforeValidator(_check_email),      Hint("email address, lowercased")]
PhoneE164 = Annotated[str, BeforeValidator(_check_phone_e164), Hint("phone in E.164 ('+15551234567'); bare 10/11-digit numbers are normalised as US")]
URL       = Annotated[str, BeforeValidator(_check_url),        Hint("http(s) URL")]


# ---- Finance / tax markers ----------------------------------------------

def _check_currency_code(v):
    if v is None:
        return v
    s = str(v).strip().upper()
    if pycountry.currencies.get(alpha_3=s) is None:
        raise ValueError(f"not an ISO 4217 currency code: {v!r}")
    return s


_EIN_RE = _re.compile(r"^\d{2}-\d{7}$")


def _check_ein(v):
    """Normalise to NN-NNNNNNN; accept 9 raw digits and insert the dash."""
    if v is None:
        return v
    raw = str(v).strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) != 9:
        raise ValueError(f"not a US EIN (need 9 digits): {v!r}")
    canonical = f"{digits[:2]}-{digits[2:]}"
    if not _EIN_RE.match(canonical):
        raise ValueError(f"not a US EIN after normalisation: {v!r}")
    return canonical


CurrencyCode = Annotated[str, BeforeValidator(_check_currency_code), Hint("ISO 4217 alpha-3 currency code, uppercase (e.g. 'USD', 'EUR')")]
EIN          = Annotated[str, BeforeValidator(_check_ein),           Hint("US Employer Identification Number, normalised to 'NN-NNNNNNN'")]
