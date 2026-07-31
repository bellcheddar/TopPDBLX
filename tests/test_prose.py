"""Tests for stripping narrative prose off a reagent clause.

The stripper runs only as a retry after a clause has already failed to identify, so its risk
profile is one-sided: it can turn an unidentified component into a identified one, and cannot turn a
identified one into anything else. These tests hold that line, because a pattern that trims too
much would be invisible in the coverage number.
"""

from __future__ import annotations

import pytest

from toppdblx.parse.prose import strip_prose


@pytest.mark.parametrize("clause,expected", [
    ("crystal conditions were 100 mm bis-tris propane", "100 mm bis-tris propane"),
    ("then transferred to 100 mm tris-hcl", "100 mm tris-hcl"),
    ("protein buffer: 20 mm tris", "20 mm tris"),
    ("solution composed by 25mm hepes", "25mm hepes"),
    ("contained 0.1 m hepes", "0.1 m hepes"),
    ("glycerol added as cryoprotectant", "glycerol"),
])
def test_narrative_is_stripped_from_around_the_chemistry(clause, expected):
    assert strip_prose(clause) == expected


@pytest.mark.parametrize("clause", [
    "sodium acetate",
    "20% peg 3350",
    "0.1 M HEPES pH 7.5",
    "tris base",
    "ammonium sulfate",
    "1,2-propanediol",
])
def test_a_plain_reagent_clause_is_left_completely_alone(clause):
    """None rather than the original, so the caller can tell a retry from a no-op. A reagent name
    has no verb or colon boundary for the patterns to anchor on, which is why they are safe."""
    assert strip_prose(clause) is None


def test_a_clause_with_no_chemistry_left_returns_nothing():
    """"the complex was" reduces to "the complex", which is not chemistry and would only waste a
    lookup. Returning it would also risk it matching something by accident."""
    assert strip_prose("the complex was") is None


def test_stacked_narrative_is_stripped_repeatedly():
    assert strip_prose("the crystallisation solution was 0.1 m sodium citrate") \
        == "0.1 m sodium citrate"


def test_empty_input_is_safe():
    assert strip_prose("") is None
    assert strip_prose(None) is None
