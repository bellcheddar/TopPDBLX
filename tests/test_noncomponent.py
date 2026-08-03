"""Tests for recognising clauses that contain no reagent.

The asymmetry that shapes every test here: leaving a real reagent unidentified is **visible** in
the coverage report, but classifying a real reagent as "not a component" **silently deletes
chemistry**. So the tests weight false positives far more heavily than false negatives, and the
recall tests below are deliberately the shorter half.
"""

from __future__ import annotations

import pytest

from toppdblx.parse.noncomponent import classify

# --- must never be discarded: real chemistry ---------------------------------------------

@pytest.mark.parametrize("name", [
    "peg",                       # bare PEG: molecular weight unstated, but a real precipitant
    "mpd",                       # two-letter reagent, would match the fragment rule
    "spg",                       # succinate-phosphate-glycine buffer system
    "tris",
    "nad",
    "sodium chloride",
    "methyl-2,4-pentanediol",    # an MPD synonym missing from the lexicon, still a reagent
    "ca(oac)2",
    "bacl2",
    "peg 3400",
    "1,3-propanediol",
    "sodium citrate tribasic dehydrate",
    "imidazole malate",
    "glycerol ethoxylate",
    "potassium/sodium tartrate",
])
def test_real_reagents_are_never_classified_as_non_components(name):
    assert classify(name) is None, f"{name!r} is chemistry and must survive"


def test_a_method_phrase_beside_a_reagent_does_not_discard_the_reagent():
    """The substring rule can fire on a clause that also names a substance. A concentration is
    strong evidence a substance is being named, so it must win: discarding this would lose the
    sodium chloride entirely."""
    assert classify("sodium chloride was mixed with", has_quantity=True) is None


def test_protein_as_a_modifier_is_not_an_unnamed_macromolecule():
    """"protein" alone names no substance, but the unnamed rule is anchored to the whole clause,
    so a real reagent that merely contains the word survives."""
    assert classify("protein buffer with sodium chloride") is None


# --- must be discarded: not chemistry ----------------------------------------------------

@pytest.mark.parametrize("name,reason", [
    ("streak seeded", "method_text"),
    ("crystal obtained by streak-seeding", "method_text"),
    ("the crystals grew extremely slowly", "method_text"),
    ("reproducibility was improved by seeding", "method_text"),
    ("small tubes", "method_text"),
    ("batch", "method_text"),
    ("hanging drop", "method_text"),
    ("protein", "unnamed_macromolecule"),
    ("inhibitor", "unnamed_macromolecule"),
    ("compound", "unnamed_macromolecule"),
    ("ligands", "unnamed_macromolecule"),
    ("hampton research index screen", "screen_reference"),
    ("molecular dimensions morpheus screen condition d8", "screen_reference"),
    ("buffer system 3", "screen_reference"),
    ("proplex condition b12", "screen_reference"),
    ("as given in reference 4", "publication_reference"),
    ("see also: sawyer et al", "publication_reference"),
    ("na", "splitter_fragment"),
    ("ca", "splitter_fragment"),
    ("nh4", "splitter_fragment"),
    ("po4", "splitter_fragment"),
    ("unknown", "splitter_fragment"),
    ("---", "splitter_fragment"),
])
def test_non_chemistry_is_classified_with_the_right_reason(name, reason):
    assert classify(name) == reason


def test_a_concentration_does_not_rescue_an_unnamed_ligand():
    """"1 mM inhibitor" is the case that motivated splitting the quantity veto. The depositor
    gave a role, not a compound: no lexicon entry can ever match it, and the concentration does
    not make it identifiable. Measured at 340 occurrences of bare "inhibitor" carrying a
    quantity."""
    assert classify("inhibitor", has_quantity=True) == "unnamed_macromolecule"


def test_a_concentration_does_not_rescue_a_screen_reference():
    assert classify("buffer system 3", has_quantity=True) == "screen_reference"


def test_a_bare_counter_ion_is_a_fragment_even_with_a_concentration():
    """"0.2 M na" names no salt, because the counter-ion is what makes it one. Honest to flag as
    a splitter artefact rather than to report it as a reagent the lexicon lacks."""
    assert classify("na", has_quantity=True) == "splitter_fragment"


# --- edges -------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["", "   ", None])
def test_empty_input_is_not_a_verdict(name):
    """Absent text is not evidence of anything, so it must not be labelled non-chemistry."""
    assert classify(name) is None


# --- text that is not chemistry, found in the unidentified head ----------------------------

@pytest.mark.parametrize("name,reason", [
    ("millimolar", "splitter_fragment"),      # a unit that became a name: a splitter bug
    ("molar", "splitter_fragment"),
    ("ul", "splitter_fragment"),
    ("ul protein", "method_text"),
    ("ul reservoir", "method_text"),
    ("20 ul drop", "method_text"),
    ("soaked", "method_text"),
    ("then soaked", "method_text"),
    ("precipitant mix", "screen_reference"),
    ("precipitant mix 4", "screen_reference"),
    ("solution b", "screen_reference"),
])
def test_apparatus_units_and_recipe_references_are_not_reagents(name, reason):
    """Each of these sat in the unidentified head with real corpus weight, counted as a reagent
    the parser had failed to identify. `millimolar` is the clearest case: it is a unit, so its
    presence as a component name is evidence of a clause split between a number and its
    reagent, not of a missing lexicon entry."""
    assert classify(name) == reason


@pytest.mark.parametrize("name", [
    "mega 8", "peg smear medium", "sodium chloride", "ammonium sulfate",
    "glycerol", "mpd", "tris", "peg 3350", "lithium sulfate", "sodium malonate",
])
def test_the_new_patterns_do_not_swallow_real_reagents(name):
    """The patterns above are anchored to whole clauses, and the risk of over-reach is that a
    real reagent silently disappears rather than showing up as unidentified. `mega 8` is the
    sharpest test: it looks like an apparatus note and is a detergent."""
    assert classify(name) is None


# Narrative and split method phrases, 2026-08-03. Ranking the 71,194 unidentified components
# showed ~2,400 that are not chemistry and were being scored as reagents the parser failed to
# identify: single words torn out of "VAPOR DIFFUSION, HANGING DROP" by the splitter, free text
# some depositors leave in the details field, durations, and roles rather than substances.

@pytest.mark.parametrize("clause", [
    "vapor", "vapour", "diffusion", "hanging", "sitting", "drop", "well", "plate",
])
def test_a_word_torn_out_of_a_method_phrase_is_method_text(clause):
    """The splitter breaks "VAPOR DIFFUSION, HANGING DROP" on the comma and again on the space,
    leaving each word standing alone as though it were a reagent."""
    assert classify(clause) == "method_text"


@pytest.mark.parametrize("clause", [
    "results", "discussion for this structure", "respectively", "then", "anaerobic",
    "if needed", "no buffer", "prior to data collection", "batch crystallization",
    "several conditions resulted in crystals", "grown", "crystallized", "solubilized",
])
def test_narrative_left_in_the_details_field_is_method_text(clause):
    assert classify(clause) == "method_text"


@pytest.mark.parametrize("clause", ["days", "2 days", "overnight", "48 hours", "1-2 days"])
def test_a_duration_is_not_a_substance(clause):
    assert classify(clause) == "method_text"


@pytest.mark.parametrize("clause", [
    "precipitant", "crystallant", "buffer", "protein buffer", "peptidic ligand",
    "crystallizing agent", "heavy atom",
])
def test_a_role_is_not_a_substance(clause):
    """The depositor named what it did, not what it was. No lexicon entry can ever match."""
    assert classify(clause) == "unnamed_macromolecule"


@pytest.mark.parametrize("clause", [
    # Vendor stock mixtures. Real reagents with real compositions, and the Morpheus stock table
    # can expand them -- so they must NOT be swallowed as method text.
    "divalents", "divalent cations", "monosaccharides", "alcohols", "halogens", "nps",
    # Reagents whose names collide with the prose above.
    "sodium acetate", "peg 3350", "glycerol", "tris", "mpd", "dtt",
])
def test_real_reagents_and_vendor_stocks_survive(clause):
    assert classify(clause) is None


def test_no_anchored_rule_fires_on_a_clause_carrying_a_quantity_that_names_a_reagent():
    """A concentration is strong evidence a substance is being named. The anchored rules
    deliberately ignore `has_quantity`, so they must never match a real reagent name."""
    for clause in ("0.1 M sodium acetate", "20% peg 3350", "1 mM dtt", "2.5 M ammonium sulfate"):
        assert classify(clause, has_quantity=True) is None
