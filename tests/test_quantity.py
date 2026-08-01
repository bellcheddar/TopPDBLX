"""Tests for quantity extraction and unit inference.

Unit inference is the highest-consequence rule in the parser: roughly 80% of percentage
concentrations in this corpus carry no w/v or v/v marker, so the default decides the unit
for four values in five. It is tested here on its own, and measured separately in the WP8
audit, because a systematic error would be invisible in aggregate parse accuracy.
"""

from __future__ import annotations

import pytest

from toppdblx.parse.quantity import extract, infer_unit, is_implausible


# --- extraction -----------------------------------------------------------

@pytest.mark.parametrize("clause,value,unit", [
    ("0.2 m sodium citrate", 0.2, "molar"),
    ("100 mm tris", 100.0, "millimolar"),
    ("20% peg 3350", 20.0, "percent_unspecified"),
    ("20 % w/v peg 6000", 20.0, "percent_w_v"),
    ("15 %(v/v) glycerol", 15.0, "percent_v_v"),
    ("5 mg/ml protein", 5.0, "mg_ml"),
    ("30% (w/v) peg 4000", 30.0, "percent_w_v"),
])
def test_extract_value_and_unit(clause, value, unit):
    q = extract(clause)
    assert q.value == value
    assert q.unit == unit


def test_missing_unit_is_reported_as_absent_not_guessed():
    """"1.7 to 2.1 ammonium sulfate" states no unit; inference happens later, with chemistry."""
    q = extract("1.7 to 2.1 ammonium sulfate")
    assert q.unit is None
    assert q.unit_explicit is False


@pytest.mark.parametrize("clause,low,high,midpoint", [
    ("18-22% peg 3350", 18.0, 22.0, 20.0),
    ("1.7 to 2.1 ammonium sulfate", 1.7, 2.1, 1.9000000000000001),
    ("5 - 15 %(v/v) glycerol", 5.0, 15.0, 10.0),
])
def test_ranges_keep_their_endpoints(clause, low, high, midpoint):
    q = extract(clause)
    assert q.is_range
    assert (q.low, q.high) == (low, high)
    assert q.value == pytest.approx(midpoint)


def test_no_quantity_at_all():
    q = extract("ammonium sulfate")
    assert not q.found


def test_decimal_comma_is_read_as_a_decimal_point():
    assert extract("0,5 m nacl").value == 0.5


# --- unit inference -------------------------------------------------------

def test_explicit_marker_always_wins():
    unit, inferred = infer_unit("percent_v_v", "peg", 8000, "percent_w_v")
    assert (unit, inferred) == ("percent_v_v", False)


@pytest.mark.parametrize("peg_mw,expected", [
    (200, "percent_v_v"), (400, "percent_v_v"), (600, "percent_v_v"),
    (1000, "percent_w_v"), (3350, "percent_w_v"), (8000, "percent_w_v"),
])
def test_bare_percent_on_a_peg_is_identified_by_molecular_weight(peg_mw, expected):
    """Spec 6.3: below ~600 a PEG behaves as an organic, above it as a polymer."""
    unit, inferred = infer_unit("percent_unspecified", "peg", peg_mw, None)
    assert unit == expected
    assert inferred is True


@pytest.mark.parametrize("chem_class,expected", [
    ("organic", "percent_v_v"),
    ("polyol", "percent_v_v"),
    ("salt", "percent_w_v"),
    ("buffer", "percent_w_v"),
    ("detergent", "percent_w_v"),
])
def test_bare_percent_is_identified_by_chemical_class(chem_class, expected):
    unit, inferred = infer_unit("percent_unspecified", chem_class, None, None)
    assert unit == expected
    assert inferred is True


def test_unknown_chemistry_defaults_to_weight_volume_but_is_flagged():
    unit, inferred = infer_unit("percent_unspecified", None, None, None)
    assert unit == "percent_w_v"
    assert inferred is True


def test_absent_unit_falls_back_to_the_curated_default():
    """"1.7 to 2.1 ammonium sulfate" means molar, from the reagent's default."""
    unit, inferred = infer_unit(None, "salt", None, "molar")
    assert (unit, inferred) == ("molar", True)


def test_absent_unit_with_no_default_stays_absent():
    unit, inferred = infer_unit(None, None, None, None)
    assert unit is None
    assert inferred is False


# The plausibility floor, added 2026-08-01 after the accuracy audit found 4IBR shipping
# "10 M ZnCl2" -- which is exactly what the deposition says, and impossible.

@pytest.mark.parametrize("value,unit,expected", [
    (10, "molar", True),            # 4IBR, zinc chloride
    (3000, "molar", True),          # 7DXZ, "3000 M Sodium malonate dibasic"
    (8.1, "molar", True),
    (8.0, "molar", False),          # the boundary is inclusive: 8 M is allowed
    (4.1, "molar", False),          # saturated ammonium sulfate
    (3.4, "molar", False),          # the 99th percentile of the corpus
    (5200, "millimolar", False),    # 5.2 M: impossible for its reagent, but not unarguably so
    (9000, "millimolar", True),     # 9 M, past the floor on any reagent
    (335015, "percent_v_v", True),  # 5K79
    (101, "percent_w_v", True),
    (100, "percent_w_v", False),
    (80, "percent_w_v", False),     # the 99.9th percentile
    (875, "nanomolar", False),
    (None, "molar", False),
    (5, None, False),
    (0, "molar", False),
])
def test_implausible_concentrations(value, unit, expected):
    assert is_implausible(value, unit) is expected


def test_the_floor_is_not_a_solubility_check():
    """Documented limitation: 6 M ammonium sulfate is impossible and passes.

    Refusing it needs a per-reagent limit and the lexicon carries no solubilities. Pinned so
    the guard is not mistaken for something stronger than it is.
    """
    assert is_implausible(6, "molar") is False


# The name-number bug, found 2026-08-01 while setting the plausibility floor: "PEG3350-26%"
# parsed to 1688, the midpoint of 3350 and 26, and stripping the whole match left a bare "peg"
# that identified to nothing. So the amount was absurd and the reagent was lost with it.

@pytest.mark.parametrize("clause,value,unit", [
    ("peg3350-26%", 26.0, "percent_unspecified"),
    ("peg 3350 - 23% w/v", 23.0, "percent_w_v"),
    ("peg 6000-20%", 20.0, "percent_unspecified"),
    # Not only PEG: the trailing digit of a formula is caught the same way.
    ("mgso4 - 0.15m", 0.15, "molar"),
    ("nano3 - 0.1m", 0.1, "molar"),
])
def test_a_trailing_descending_pair_is_a_name_and_an_amount(clause, value, unit):
    found = extract(clause)
    assert (found.value, found.unit) == (value, unit)
    assert found.is_range is False


@pytest.mark.parametrize("clause,value", [
    ("2.0-1.8 m ammonium sulfate", 1.9),
    ("8-6% peg3350", 7.0),
    ("0.2-0.18m ammonium sulfate", 0.19),
])
def test_a_leading_descending_pair_is_still_a_backwards_range(clause, value):
    """Confined to the trailing form on purpose. A depositor writing "2.0-1.8 M" means a real
    range and its midpoint is correct; 96 of these are in the corpus against 12 of the bug."""
    found = extract(clause)
    assert found.is_range is True
    assert found.value == pytest.approx(value)
