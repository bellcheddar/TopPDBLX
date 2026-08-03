"""Tests for the shared condition-text normalisation.

Every case here is a real string or a real pattern taken from the corpus ranking, not an
invented one. The WP3 rule parser will build directly on these functions, so the awkward
cases are pinned down now rather than rediscovered later.
"""

from __future__ import annotations

import pytest

from toppdblx.parse import lexicon as lexicon_module
from toppdblx.parse.rules import RuleParser

from toppdblx.parse.text import (
    classify,
    clauses,
    clauses_detailed,
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

# The truncation is gated on the caller recognising the head as a reagent, so these pass a
# stand-in for the lexicon rather than the real one: the gate is the contract under test, not
# the lexicon's contents.
KNOWN = {"1-butanol", "peg 8000", "nacl"}


def known(head: str) -> bool:
    return tidy_name(strip_quantity(split_trailing_ph(head)[0])) in KNOWN


def test_reagent_survives_trailing_setup_prose():
    """7O5Q and 7NRJ: "10% 1-BUTANOL mixed with the 10 mg/mL protein stock at 1:1 ratio".

    `_PROTEIN` matches "mixed ... with", so the whole clause was called protein_or_setup and the
    butanol went with it. The prose is cut off instead and the component kept.
    """
    found = clauses(
        "0.1M HEPES pH 8.0, 10% PEG 8000, 10% 1-BUTANOL mixed with the 10 mg/mL protein "
        "stock at 1:1 ratio.", known)
    assert "10% 1-butanol" in found
    assert classify("10% 1-butanol") == "reagent"


def test_no_gate_means_no_truncation():
    """Every caller that does not hold a lexicon keeps the behaviour it had before."""
    text = ("10% 1-BUTANOL mixed with the 10 mg/mL protein stock at 1:1 ratio.")
    assert clauses(text) == ["10% 1-butanol mixed with the 10 mg/ml protein stock at 1:1 ratio."]


def test_an_unrecognised_head_is_not_truncated():
    """The shape test alone yields "ul", "protein at 10" and "nacl was": 3,752 junk heads
    against 221 real ones over 60,000 conditions. Only a head the caller knows is cut to."""
    assert clauses("10 mg/ml lysozyme mixed with the reservoir at 1:1", known) == [
        "10 mg/ml lysozyme mixed with the reservoir at 1:1"]


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
    found = clauses("20% PEG 3350, protein solution: 15 mg/ml in 10 mM tris", known)
    assert "20% peg 3350" in found
    assert any(c.startswith("protein solution:") for c in found)
    assert "12-15" not in found
    for clause in found:
        if clause.startswith("protein solution:"):
            assert classify(clause) == "protein_or_setup"


def test_setup_prose_alone_is_not_split_into_a_component():
    """The head must carry both a number and a name, or prose splits into more prose."""
    assert clauses("mother liquor mixed with protein at 1:1", known) == [
        "mixed with protein at 1:1"]


@pytest.mark.parametrize("clause,name", [
    ("peg3350-26%", "peg 3350"),
    ("peg 3350 - 23% w/v", "peg 3350"),
    ("peg 6000-20%", "peg 6000"),
    ("mgso4 - 0.15m", "mgso4"),
    ("nano3 - 0.1m", "nano3"),
])
def test_a_trailing_descending_pair_returns_its_first_number_to_the_name(clause, name):
    """The other half of the same fix. `quantity.extract` reads the amount and this reads the
    name, and they must agree: reading them off different splits of one string is what once put
    17,256 molecular weights into the concentration column."""
    assert strip_quantity(clause) == name


def test_a_name_ending_in_digits_still_keeps_them():
    """The regression the anchoring comment exists for: "peg 8000" is a name, not an amount."""
    assert strip_quantity("peg 8000") == "peg 8000"
    assert strip_quantity("15-20% peg 3350") == "peg 3350"


@pytest.mark.parametrize("clause,name", [
    ("5000 mme", "peg mme 5000"),
    ("2000 mme", "peg mme 2000"),
    ("5000 mme, 0.1 m hepes", "peg mme 5000, 0.1 m hepes"),
])
def test_a_bare_molecular_weight_before_mme_regains_its_peg(clause, name):
    """"PEG Smear Medium (PEG 2000, 3350, 4000, and 5000 MME)" splits so the last member loses
    its PEG. Read as a leading quantity it became MME -- an additive defaulting to millimolar --
    at 5000 mM, five molar of a reagent that is really PEG MME 5000."""
    assert strip_quantity(clause) == name


def test_an_explicit_peg_mme_is_unaffected():
    assert strip_quantity("5% peg mme 5000") == "peg mme 5000"


# Missing-separator splitting, 2026-08-03. 5QNI writes
# "11-13% PEG 8000 5-7.5% GLYCEROL 1 MM CUCL2 100 MM SODIUM CACODYLATE" with no commas at all,
# and 8P93 writes "2.2M ammonium phosphate 0.1M TrisHCl". `_SPLIT` has nothing to break on, so
# four fully quantified reagents became one clause that identified to nothing. 3,603 clauses in
# the corpus are like this.

def _split(text):
    lexicon = lexicon_module.load()
    parser = RuleParser(lexicon)
    return [c for c, _ in clauses_detailed(text, parser._head_is_reagent)]


def test_reagents_jammed_together_with_no_separator_are_split():
    assert _split("11-13% PEG 8000 5-7.5% GLYCEROL 1 MM CUCL2 100 MM SODIUM CACODYLATE") == [
        "11-13% peg 8000", "5-7.5% glycerol", "1 mm cucl2", "100 mm sodium cacodylate"]


def test_a_comma_followed_by_a_digit_still_splits_when_a_unit_follows():
    """`_SPLIT` protects "peg 6,000" by refusing to break on a comma before a digit, which also
    left "20 mm tris,1 mm edta" whole."""
    assert _split("2.2M ammonium phosphate 0.1M TrisHCl") == [
        "2.2m ammonium phosphate", "0.1m trishcl"]


@pytest.mark.parametrize("text", [
    "1.5m to 1.8m nacl",          # one range written with two quantities
    "12% to 20% peg 1450",
    "20% - 30% 2-methyl-2,4-pentanediol",
])
def test_a_range_is_not_two_components(text):
    """1,066 clauses are ranges of this shape. Splitting any of them invents a reagent from the
    upper bound and leaves a fragment behind."""
    assert _split(text) == [text]


@pytest.mark.parametrize("text", [
    "20% peg 3350",               # a single quantified reagent
    "peg 3350 20%",               # the amount trails its reagent; splitting would orphan it
    "1,6-hexanediol 10%",         # the digits are locants, not a quantity
    "0.1 m tris ph 8.5",          # a pH is not a unit
    "30% peg 20,000",             # a molecular weight with a comma
])
def test_nothing_else_is_split(text):
    assert _split(text) == [text]


def test_a_bare_number_after_a_reagent_is_a_molecular_weight_not_a_new_component():
    """The unit is what marks a new component. "PEG 8000" must never become "PEG" + "8000"."""
    assert _split("20% peg 8000 0.1 m hepes") == ["20% peg 8000", "0.1 m hepes"]
