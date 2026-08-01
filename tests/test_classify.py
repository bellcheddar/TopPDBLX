"""Tests for the seven-class condition classification.

The rule is deliberately small: presence of a precipitant family, no thresholds, and anything
that cannot be classified honestly is Unclassified. These assert exactly that, because the
previous version accreted three thresholds that were never in the brief and made the answer
impossible to predict.
"""

from __future__ import annotations

import pytest

from toppdblx.assign.classify import UNCLASSIFIED, classify_condition


def comp(name="SODIUM_CHLORIDE", chem_class="salt", concentration=0.2, unit="molar",
         role="salt", premix_id=None):
    return {"name_canonical": name, "chem_class": chem_class, "concentration": concentration,
            "unit": unit, "role": role, "premix_id": premix_id}


def test_a_peg_is_a_peg_whatever_its_size():
    """No molecular-weight cutoff. The previous rule reclassified PEG under 600 as an organic,
    which meant a PEG 400 condition was not a PEG condition."""
    for mw_name in ("PEG_400", "PEG_3350", "PEG_20000"):
        assert classify_condition([comp(mw_name, "peg", 20, "percent_w_v")])[0] == "PEG"


def test_a_peg_is_a_peg_whatever_its_concentration():
    """No percentage threshold. A 2% PEG counts."""
    assert classify_condition([comp("PEG_3350", "peg", 2, "percent_w_v")])[0] == "PEG"


def test_a_salt_is_a_salt_whatever_its_chemistry_and_amount():
    """No Hofmeister family and no molarity threshold: 0.05 M counts as much as 2 M."""
    assert classify_condition([comp("SODIUM_CHLORIDE", "salt", 0.05, "molar")])[0] == "Salt"
    assert classify_condition([comp("AMMONIUM_SULFATE", "salt", 2.0, "molar")])[0] == "Salt"


def test_families_combine_into_the_seven_classes():
    assert classify_condition([comp("PEG_3350", "peg", 20, "percent_w_v"),
                               comp()])[0] == "Salt/PEG"
    assert classify_condition([comp("PEG_3350", "peg", 20, "percent_w_v"),
                               comp("MPD", "organic", 30, "percent_v_v"),
                               comp()])[0] == "Organic/PEG/Salt"


def test_a_buffer_neither_classifies_nor_disqualifies():
    """Spec 6.3: at 0.1 M a buffer sets the pH rather than precipitating anything."""
    assert classify_condition([comp("PEG_3350", "peg", 20, "percent_w_v"),
                               comp("HEPES", "buffer", 0.1, "molar", role="buffer")])[0] == "PEG"


def test_a_buffer_alone_is_unclassified():
    label, reason = classify_condition([comp("HEPES", "buffer", 0.1, "molar", role="buffer")])
    assert label == UNCLASSIFIED and reason == "no_precipitant"


# --- the honest refusals -------------------------------------------------------------------

def test_a_mixture_is_unclassified():
    """Settles spec 6.4. Morpheus and PACT carry an acid mix, an alcohol mix and a buffer system
    at once, and do not fit a seven-class taxonomy."""
    label, reason = classify_condition([comp("TACSIMATE", "premix", 10, "percent_v_v",
                                             premix_id="TACSIMATE")])
    assert label == UNCLASSIFIED and reason == "mixture"


def test_an_unidentified_reagent_makes_the_whole_condition_unclassified():
    """A name the lexicon does not recognise could be anything, so nothing about the condition
    can be asserted on the evidence."""
    label, reason = classify_condition([comp("PEG_3350", "peg", 20, "percent_w_v"),
                                        comp(None, None, 1, "molar")])
    assert label == UNCLASSIFIED and reason == "unidentified_reagent"


def test_a_precipitant_with_no_amount_is_unclassified():
    """About 22,000 components name a precipitant and never say how much."""
    label, reason = classify_condition([comp("PEG_3350", "peg", None, None)])
    assert label == UNCLASSIFIED and reason == "no_amount"


def test_a_missing_amount_on_a_non_precipitant_is_not_fatal():
    """The amount is only required of the families that decide the class, so an additive with no
    stated concentration does not sink an otherwise measured condition."""
    assert classify_condition([comp("PEG_3350", "peg", 20, "percent_w_v"),
                               comp("BENZAMIDINE", "additive", None, None,
                                    role="additive")])[0] == "PEG"


def test_method_text_is_ignored_rather_than_disqualifying():
    assert classify_condition([comp("PEG_3350", "peg", 20, "percent_w_v"),
                               comp(None, None, None, None,
                                    role="not_a_component")])[0] == "PEG"


def test_an_empty_condition_is_unclassified():
    assert classify_condition([])[0] == UNCLASSIFIED


# Found by the first classification accuracy audit, 2026-08-01, on 1V6H: "10% w/w of glycerol
# was added for cryoprotection" was read correctly and then counted as an organic, turning a PEG
# condition into Organic/PEG. Glycerol is a polyol and polyols map to the organic family, so
# every explicitly stated glycerol cryo did this: 176 conditions across the corpus.

def cryo(name="GLYCEROL", chem_class="polyol", concentration=10, unit="percent_w_v",
         evidence="explicit"):
    return {"name_canonical": name, "chem_class": chem_class, "concentration": concentration,
            "unit": unit, "role": "cryo", "premix_id": None, "cryo_evidence": evidence}


def test_an_explicit_cryoprotectant_does_not_name_the_class():
    """1V6H. A cryoprotectant was added after the crystal grew; it precipitated nothing."""
    condition = [comp("PEG_20000", "peg", 16.5, "percent_w_v"), cryo()]
    assert classify_condition(condition)[0] == "PEG"


def test_an_inferred_cryoprotectant_still_counts():
    """17,708 components are role=cryo by inference against 2,112 by the depositor's own words.

    Excluding the inferred ones would move 14,825 conditions rather than 176, on a guess: a
    glycerol the pipeline merely suspects is a cryoprotectant may be a real precipitant, and
    dropping it would invent an Unclassified where there was a genuine reading.
    """
    condition = [comp("PEG_20000", "peg", 16.5, "percent_w_v"), cryo(evidence="inferred")]
    assert classify_condition(condition)[0] == "Organic/PEG"


def test_a_condition_that_is_only_an_explicit_cryo_is_unclassified():
    """Excluding it must leave no precipitant rather than silently leaving the class behind."""
    label, reason = classify_condition([cryo()])
    assert (label, reason) == (UNCLASSIFIED, "no_precipitant")
