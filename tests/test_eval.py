"""Tests for audit sampling and metric helpers."""

from __future__ import annotations

import pytest

from toppdblx.eval.audit_metrics import wilson_interval
from toppdblx.eval.sample_audit import allocate, confidence_band, era


# --- stratification helpers ----------------------------------------------

@pytest.mark.parametrize("value,band", [
    (1.0, "perfect"), (0.9999, "perfect"), (0.99, "high"), (0.75, "high"),
    (0.6, "medium"), (0.5, "medium"), (0.2, "low"), (0.0, "low"), (None, "unknown"),
])
def test_confidence_band(value, band):
    assert confidence_band(value) == band


@pytest.mark.parametrize("date,label", [
    ("1997-05-01T00:00:00Z", "pre-2000"),
    ("2008-07-08T00:00:00Z", "2000s"),
    ("2015-01-01T00:00:00Z", "2010s"),
    ("2024-10-30T00:00:00Z", "2020s"),
    (None, "unknown"),
])
def test_era(date, label):
    assert era(date) == label


def test_era_uses_the_date_it_is_given_not_a_revision():
    """Guards the bug this had: using entry_version put 1997 entries in the 2020s."""
    assert era("1997-05-01T00:00:00Z") == "pre-2000"


# --- allocation -----------------------------------------------------------

def test_every_populated_stratum_gets_at_least_the_floor():
    counts = {("a",): 10000, ("b",): 5, ("c",): 1}
    allocation = allocate(counts, size=100, floor=3)
    assert allocation[("b",)] >= 3
    assert allocation[("c",)] == 1          # cannot exceed the population
    assert allocation[("a",)] > allocation[("b",)]


def test_allocation_never_exceeds_a_stratum_population():
    counts = {("a",): 2, ("b",): 3}
    allocation = allocate(counts, size=1000, floor=3)
    for key, value in allocation.items():
        assert value <= counts[key]


def test_allocation_roughly_respects_the_requested_size():
    counts = {(chr(97 + i),): 500 for i in range(10)}
    allocation = allocate(counts, size=200, floor=3)
    assert 180 <= sum(allocation.values()) <= 200


def test_allocation_with_size_below_the_floor_total():
    """More strata than budget: everything still gets something, nothing goes negative."""
    counts = {(chr(97 + i),): 50 for i in range(30)}
    allocation = allocate(counts, size=10, floor=3)
    assert all(v >= 0 for v in allocation.values())


# --- Wilson interval ------------------------------------------------------

def test_wilson_interval_brackets_the_point_estimate():
    low, high = wilson_interval(90, 100)
    assert low < 0.9 < high


def test_wilson_interval_stays_inside_zero_and_one():
    """The normal approximation goes out of range here; Wilson must not."""
    for successes, total in [(0, 10), (10, 10), (1, 1000), (999, 1000)]:
        low, high = wilson_interval(successes, total)
        assert 0.0 <= low <= high <= 1.0


def test_wilson_interval_narrows_as_the_sample_grows():
    narrow = wilson_interval(900, 1000)
    wide = wilson_interval(9, 10)
    assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


def test_wilson_interval_on_an_empty_sample():
    assert wilson_interval(0, 0) == (0.0, 0.0)

