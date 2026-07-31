"""Tests for the guards on the lexicon-gap recommender.

Every dropdown in the audit is pre-selected and there is an "accept every recommendation" button,
so a wrong recommendation is not a suggestion the expert will catch: it is a wrong answer that
gets accepted in bulk. Each guard here exists because the unguarded matcher actually proposed the
mistake named in the test.

The complementary claim matters just as much: a guard must not make the *correct* answer
unreachable. Guards decide what is recommended, never what is available.
"""

from __future__ import annotations

import pytest

from toppdblx.parse.lexicon_questions import (expand_formula, is_ambiguous, match_is_safe,
                                              normalise_for_match)


# --- numbers are chemistry, not decoration ------------------------------------------------

@pytest.mark.parametrize("name,candidate", [
    ("peg 3400", "peg 400"),        # proposed unguarded: an eightfold molecular weight error
    ("peg 3500", "peg 300"),
    ("peg 2000", "peg 20000"),
    ("1,3-propanediol", "1,2 propanediol"),   # the isomer error, as with 1,4-butanediol before
    ("2,3-butanediol", "1,4 butanediol"),
])
def test_names_whose_numbers_differ_are_never_recommended(name, candidate):
    assert match_is_safe(name, normalise_for_match(candidate)) is False


def test_identical_numbers_still_match():
    assert match_is_safe("peg 3350", normalise_for_match("peg 3350")) is True


def test_names_without_numbers_are_unaffected_by_the_digit_rule():
    assert match_is_safe("sodium formate", normalise_for_match("sodium formate")) is True


# --- a different element or acid is a different chemical ----------------------------------

@pytest.mark.parametrize("name,candidate", [
    ("strontium chloride", "sodium chloride"),     # proposed unguarded, different cation
    ("barium chloride", "calcium chloride"),
    ("sodium maleate", "sodium malate"),           # maleate and malate are different compounds
    ("malonate", "malate"),
    ("sodium acetate", "sodium formate"),
])
def test_names_with_a_different_element_or_acid_are_never_recommended(name, candidate):
    assert match_is_safe(name, normalise_for_match(candidate)) is False


def test_the_same_salt_spelled_differently_still_matches():
    """The guard must not fire when both names name the same chemistry."""
    assert match_is_safe("sodium chloride", normalise_for_match("sodium chloride")) is True


def test_the_guard_stays_silent_when_only_one_side_names_an_ion():
    """"tacsimate" against "tascimate" carries no ion word on either side, so the ion rule has
    nothing to say and must not block an obvious typo match."""
    assert match_is_safe("tascimate", normalise_for_match("tacsimate")) is True


# --- a family name is not a member --------------------------------------------------------

@pytest.mark.parametrize("name", [
    "peg", "polyethylene glycol", "phosphate", "citrate buffer", "propanediol", "butanediol",
])
def test_family_names_are_flagged_ambiguous_rather_than_identified(name):
    """"phosphate" names no counter-ion and "peg" no molecular weight. Recommending a specific
    entry would invent data the depositor never supplied: 720 components say only "peg"."""
    assert is_ambiguous(name) is True


@pytest.mark.parametrize("name", ["peg 3350", "sodium phosphate", "1,2-propanediol"])
def test_a_fully_specified_name_is_not_ambiguous(name):
    assert is_ambiguous(name) is False


# --- formula expansion ---------------------------------------------------------------------

@pytest.mark.parametrize("formula,expected", [
    ("bacl2", "barium chloride"),
    ("ca(oac)2", "calcium acetate"),
    ("nacl", "sodium chloride"),
    ("k2hpo4", "dipotassium hydrogen phosphate"),
    # Conventionally "ammonium sulfate", not "diammonium sulfate": the stoichiometric prefix is
    # dropped for this salt in normal usage, and it is the unprefixed form that matches the
    # lexicon entry. K2HPO4 keeps its prefix because "potassium hydrogen phosphate" would be
    # ambiguous between the mono- and di-basic salts.
    ("(nh4)2so4", "ammonium sulfate"),
])
def test_common_formulae_expand_to_names(formula, expected):
    assert expand_formula(formula) == expected


@pytest.mark.parametrize("text", ["peg 3350", "streak seeded", "hepes", ""])
def test_non_formulae_expand_to_nothing_rather_than_a_guess(text):
    assert expand_formula(text) is None


# --- normalisation ---------------------------------------------------------------------------

def test_hydration_state_is_ignored_for_matching():
    """"sodium citrate tribasic dehydrate" is sodium citrate. Hydration state and grade words
    appear in deposition text and never in lexicon names, and never distinguish two reagents."""
    assert (normalise_for_match("sodium citrate tribasic dehydrate")
            == normalise_for_match("sodium citrate"))


def test_punctuation_is_ignored_for_matching():
    assert normalise_for_match("na-formate") == normalise_for_match("na formate")
