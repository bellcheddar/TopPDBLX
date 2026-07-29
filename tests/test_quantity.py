"""Tests for quantity extraction and unit inference.

Unit inference is the highest-consequence rule in the parser: roughly 80% of percentage
concentrations in this corpus carry no w/v or v/v marker, so the default decides the unit
for four values in five. It is tested here on its own, and measured separately in the WP8
audit, because a systematic error would be invisible in aggregate parse accuracy.
"""

from __future__ import annotations

import pytest

from toppdblx.parse.quantity import extract, infer_unit


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
def test_bare_percent_on_a_peg_is_resolved_by_molecular_weight(peg_mw, expected):
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
def test_bare_percent_is_resolved_by_chemical_class(chem_class, expected):
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
