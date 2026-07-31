"""Quantity and unit extraction from a condition clause.

Separated from the rule parser because this is where the corpus is most treacherous and
the logic deserves its own tests.

The load-bearing decision is unit inference. Roughly **80% of percentage concentrations in
this corpus carry no w/v or v/v marker at all**, so the defaulting rule decides the unit for
four values in five. It is applied from the reagent's chemistry, never guessed globally,
and every inferred unit is flagged so downstream work can filter on it and the WP8 audit
can measure the rule directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import regex as re

from .schema import Unit

_NUMBER = r"\d+(?:[.,]\d+)?"

# "18-22%", "1.7 to 2.1", "5 - 15 %(v/v)". The separator alternatives are ordered so the
# word forms are tried before the bare hyphen.
_RANGE_SEP = r"(?:\s*(?:to|-|–|through)\s*)"

# Anchored deliberately, at the start or at the end, mirroring `text.strip_quantity`. An
# unanchored search finds a number anywhere in the clause, so "peg 1000" yielded PEG_1000 at
# 1000% while strip_quantity correctly kept the 1000 as part of the name. That disagreement
# put 17,256 components into the database with their molecular weight as their concentration.
_QUANTITY_BODY = rf"""
    (?P<low>{_NUMBER})
    # A unit on the FIRST endpoint, but only when a range separator follows: "28% to 32%".
    # The lookahead matters: without it "30% (w/v)" consumes the % here and the (w/v) marker
    # can no longer attach, silently downgrading an explicit unit to an ambiguous one.
    (?: \s*(?P<unit_low>%|mm|m|mg\s*/\s*ml) (?={_RANGE_SEP}) )?
    (?: {_RANGE_SEP} (?P<high>{_NUMBER}) )?
    \s*
    (?P<unit>
        %\s*\(?\s*(?:w\s*/\s*v|w\s*:\s*v)\s*\)?
      | %\s*\(?\s*(?:v\s*/\s*v|v\s*:\s*v)\s*\)?
      | %\s*\(?\s*w\s*/\s*w\s*\)?
      | %
      | mg\s*/\s*ml | mg\s*/\s*l | g\s*/\s*l
      | mm | µm | um | nm | m
    )?
    (?![a-z0-9])
"""

# Leading form: the unit may be omitted ("1.7 to 2.1 ammonium sulfate" means molar).
_LEADING_QUANTITY = re.compile(rf"^\s*{_QUANTITY_BODY}", re.VERBOSE | re.IGNORECASE)
# Trailing form: a unit is required, or a reagent name ending in digits ("PEG 8000") would
# have its identity read as an amount.
_TRAILING_QUANTITY = re.compile(rf"{_QUANTITY_BODY}\s*$", re.VERBOSE | re.IGNORECASE)

_UNIT_MAP: dict[str, Unit] = {
    "%": "percent_unspecified",
    "%w/v": "percent_w_v", "%w:v": "percent_w_v", "%w/w": "percent_w_v",
    "%v/v": "percent_v_v", "%v:v": "percent_v_v",
    "m": "molar", "mm": "millimolar", "µm": "micromolar", "um": "micromolar",
    "nm": "nanomolar", "mg/ml": "mg_ml", "mg/l": "g_l", "g/l": "g_l",
}

# Percent units that the reagent's chemistry must disambiguate.
_AMBIGUOUS_PERCENT = "percent_unspecified"


@dataclass
class Quantity:
    value: Optional[float] = None
    unit: Optional[Unit] = None
    unit_explicit: bool = False
    is_range: bool = False
    low: Optional[float] = None
    high: Optional[float] = None
    span: Optional[tuple[int, int]] = None

    @property
    def found(self) -> bool:
        return self.value is not None


def _to_float(text: str) -> Optional[float]:
    try:
        # "0,5" is a decimal comma in some depositions; "6,000" was already normalised
        # to "6000" upstream by tidy_name.
        return float(text.replace(",", "."))
    except ValueError:
        return None


def _canonical_unit(raw: Optional[str]) -> Optional[Unit]:
    if not raw:
        return None
    key = re.sub(r"[\s()]", "", raw).lower()
    return _UNIT_MAP.get(key)


def extract(clause: str) -> Quantity:
    """Find the leading quantity in a clause.

    Only the first match is taken. A clause containing two quantities is a splitting
    failure upstream, not something to silently average.
    """
    match = _LEADING_QUANTITY.match(clause)
    if not match:
        trailing = _TRAILING_QUANTITY.search(clause)
        # A trailing number without a unit is part of the name, not a concentration.
        match = trailing if (trailing and (trailing.group("unit")
                                           or trailing.group("unit_low"))) else None
    if not match:
        return Quantity()

    low = _to_float(match.group("low"))
    high = _to_float(match.group("high")) if match.group("high") else None
    # "28% to 32%": the unit may sit on the first endpoint, the second, or both.
    unit = _canonical_unit(match.group("unit")) or _canonical_unit(match.group("unit_low"))

    if low is None:
        return Quantity()

    if high is not None:
        # The midpoint is the representative value; the endpoints are preserved so a
        # consumer that cares about the spread is not forced to re-parse the text.
        return Quantity(value=(low + high) / 2, unit=unit, unit_explicit=unit is not None,
                        is_range=True, low=low, high=high, span=match.span())
    return Quantity(value=low, unit=unit, unit_explicit=unit is not None,
                    span=match.span())


def infer_unit(unit: Optional[Unit], chem_class: Optional[str], peg_mw: Optional[int],
               default_unit: Optional[str]) -> tuple[Optional[Unit], bool]:
    """Identify a missing or ambiguous unit from the reagent's chemistry.

    Returns (unit, inferred). The rules, in order:

      * An explicit w/v or v/v marker always wins.
      * A bare "%" is identified by chemistry. PEGs of 600 and below behave as organic
        precipitants and are reported v/v; PEGs of 1000 and above are polymers reported
        w/v (spec 6.3). Organics and polyols are v/v. Everything else is w/v.
      * No unit at all falls back to the reagent's curated default, which is molar for
        salts and buffers: "1.7 to 2.1 ammonium sulfate" means molar.
    """
    if unit and unit != _AMBIGUOUS_PERCENT:
        return unit, False

    if unit == _AMBIGUOUS_PERCENT:
        if chem_class == "peg" and peg_mw is not None:
            return ("percent_v_v" if peg_mw <= 600 else "percent_w_v"), True
        if chem_class in ("organic", "polyol"):
            return "percent_v_v", True
        if chem_class in ("salt", "buffer", "detergent", "additive", "premix", "other"):
            return "percent_w_v", True
        # Unknown chemistry: w/v is the majority convention, but flag the inference.
        return "percent_w_v", True

    if default_unit:
        return default_unit, True          # type: ignore[return-value]
    return None, False
