"""Tests for the shared condition-text normalisation.

Every case here is a real string or a real pattern taken from the corpus ranking, not an
invented one. The WP3 rule parser will build directly on these functions, so the awkward
cases are pinned down now rather than rediscovered later.
"""

from __future__ import annotations

import pytest

from toppdblx.parse.text import (
    classify,
    clauses,
    is_noise,
    normalise,
    split_trailing_ph,
    strip_quantity,
    tidy_name,
)


# --- normalisation --------------------------------------------------------

def test_normalise_lowercases_and_collapses_whitespace():
    assert normalise("  20 %  w/v   PEG   6,000 ") == "20 % w/v peg 6,000"


def test_normalise_unifies_unicode_dashes():
    assert normalise("18–22% PEG") == "18-22% peg"


# --- clause splitting -----------------------------------------------------

def test_comma_before_a_digit_does_not_split_a_thousands_separator():
    """"PEG 6,000" must survive: splitting it would create a bogus "000" clause."""
    assert clauses("20 % w/v Polyethylene glycol 6,000") == ["20 % w/v polyethylene glycol 6,000"]


def test_splits_on_commas_semicolons_and_newlines():
    got = clauses("0.1M Sodium acetate trihydrate (pH 4.6), \n 1.0M Ammonium sulfate")
    assert got == ["0.1m sodium acetate trihydrate (ph 4.6)", "1.0m ammonium sulfate"]


def test_splits_on_spaced_double_hyphen():
    """A real separator in this corpus: "bis-tris ph 7.0 -- 30% peg3350"."""
    assert clauses("bis-tris ph 7.0 -- 30% peg3350") == ["bis-tris ph 7.0", "30% peg3350"]


def test_double_hyphen_split_does_not_break_hyphenated_reagents():
    assert clauses("0.1 M tris-hcl") == ["0.1 m tris-hcl"]
    assert clauses("bis-tris propane") == ["bis-tris propane"]


# --- trailing pH ----------------------------------------------------------

@pytest.mark.parametrize("clause,body,ph", [
    ("hepes ph 7.5", "hepes", "7.5"),
    ("hepes (ph 7.5)", "hepes", "7.5"),
    ("tris-hcl (ph 8.5)", "tris-hcl", "8.5"),
    ("tris, ph=8", "tris", "8"),
    ("mes ph 6.0-6.5", "mes", "6.0-6.5"),
    ("sodium acetate at ph 4.6", "sodium acetate", "4.6"),
])
def test_split_trailing_ph(clause, body, ph):
    assert split_trailing_ph(clause) == (body, ph)


def test_split_trailing_ph_leaves_clauses_without_one_alone():
    assert split_trailing_ph("20% peg 3350") == ("20% peg 3350", None)


# --- quantity stripping ---------------------------------------------------

@pytest.mark.parametrize("clause,name", [
    ("0.2 m sodium citrate", "sodium citrate"),
    ("20 % w/v polyethylene glycol 6,000", "polyethylene glycol 6000"),
    ("1.7 to 2.1 ammonium sulfate", "ammonium sulfate"),          # unit omitted entirely
    ("18-22% peg 3350", "peg 3350"),
    ("100 mm tris-hcl", "tris-hcl"),
    ("5 mm mncl2", "mncl2"),
    ("30% (v/v) mpd", "mpd"),
])
def test_strip_quantity(clause, name):
    assert strip_quantity(clause) == name


def test_trailing_digits_in_a_name_are_not_treated_as_a_quantity():
    """In "peg 3350" the number is identity, not amount. Losing it merges distinct reagents."""
    assert strip_quantity("20% peg 3350") == "peg 3350"
    assert strip_quantity("peg 8000") == "peg 8000"


# --- name tidying ---------------------------------------------------------

def test_balanced_chemical_formula_keeps_its_parentheses():
    """"(nh4)2so4" is ammonium sulfate; stripping the parens produced "nh4)2so4"."""
    assert tidy_name("(nh4)2so4") == "(nh4)2so4"
    assert strip_quantity("1.0 m (nh4)2so4") == "(nh4)2so4"


def test_wrapping_parentheses_are_removed():
    assert tidy_name("(ammonium sulfate)") == "ammonium sulfate"


def test_unmatched_parenthesis_is_removed():
    assert tidy_name("peg 3350)") == "peg 3350"
    assert tidy_name("(peg 3350") == "peg 3350"


def test_polymer_spacing_is_normalised_but_formulae_are_not():
    assert tidy_name("peg3350") == "peg 3350"
    assert tidy_name("peg-4000") == "peg 4000"
    # A blanket letter/digit split would wreck these.
    assert tidy_name("mgcl2") == "mgcl2"
    assert tidy_name("k2hpo4") == "k2hpo4"
    assert tidy_name("li2so4") == "li2so4"


def test_mme_suffix_is_reordered_to_match_the_lexicon():
    assert tidy_name("peg 2000mme") == "peg mme 2000"
    assert tidy_name("peg5000mme") == "peg mme 5000"


def test_parenthetical_abbreviation_is_dropped():
    assert tidy_name("polyethylene glycol (peg) 3350") == "polyethylene glycol 3350"


def test_thousands_separator_is_removed():
    assert tidy_name("peg 6,000") == "peg 6000"


# --- clause classification ------------------------------------------------

@pytest.mark.parametrize("clause,kind", [
    ("20% peg 3350", "reagent"),
    ("ph 7.5", "ph"),
    ("temperature 293k", "temperature"),
    ("293k", "temperature"),
    ("vapor diffusion", "method"),
    ("hanging drop", "method"),
    ("protein concentration 15 mg/ml", "protein_or_setup"),
    ("see also pmid 12345678", "reference_only"),
    ("as described in the paper", "reference_only"),
])
def test_classify(clause, kind):
    assert classify(clause) == kind


def test_noise_words_are_recognised():
    for word in ("solution", "the", "buffer", "crystals", "well"):
        assert is_noise(word)
    assert not is_noise("peg 3350")


# Found by the first classification accuracy audit, 2026-08-01. Each of these lost a real
# component to a splitting failure, and each was invisible until a human read the deposition
# beside the parse.

def test_reagent_survives_trailing_setup_prose():
    """7O5Q and 7NRJ: "10% 1-BUTANOL mixed with the 10 mg/mL protein stock at 1:1 ratio".

    `_PROTEIN` matches "mixed ... with", so the whole clause was called protein_or_setup and the
    butanol went with it. The prose is cut off instead and the component kept.
    """
    found = clauses(
        "0.1M HEPES pH 8.0, 10% PEG 8000, 10% 1-BUTANOL mixed with the 10 mg/mL protein "
        "stock at 1:1 ratio.")
    assert "10% 1-butanol" in found
    assert classify("10% 1-butanol") == "reagent"


def test_section_label_does_not_glue_two_components_together():
    """3ZY1: "50 MM NACL CRYSTALLIZATION BUFFER: 10% PEG 8000", with no comma before the label.

    The label glued the NaCl to the PEG, producing one clause that identified as neither.
    """
    found = clauses(
        "50 MM NACL CRYSTALLIZATION BUFFER: 10% PEG 8000, 0.1 M HEPES PH 7.5")
    assert "50 mm nacl" in found
    assert "10% peg 8000" in found


def test_protein_section_label_is_kept_so_the_clause_is_still_rejected():
    """The break is made in front of "PROTEIN SOLUTION:" but the label is left attached.

    What follows it is the protein's own buffer, not the crystallisation condition, so the
    clause must stay rejectable. Stripping the label as if it introduced components turned
    "12-15 mg/ml" into a bare "12-15" that classified as a reagent.
    """
    found = clauses("20% PEG 3350, protein solution: 15 mg/ml in 10 mM tris")
    assert "20% peg 3350" in found
    assert any(c.startswith("protein solution:") for c in found)
    assert "12-15" not in found
    for clause in found:
        if clause.startswith("protein solution:"):
            assert classify(clause) == "protein_or_setup"


def test_setup_prose_alone_is_not_split_into_a_component():
    """The head must carry both a number and a name, or prose splits into more prose."""
    assert clauses("mother liquor mixed with protein at 1:1") == [
        "mixed with protein at 1:1"]
