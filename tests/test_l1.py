"""Tests for L1 precipitant-class assignment.

The seven Top96 classes are the non-empty subsets of {Organic, PEG, Salt}, so the assignment
itself needs no curation. What needs pinning down is which components count towards a family,
because that is where the two thresholds live and where a wrong answer silently reclassifies
tens of thousands of records.
"""

from __future__ import annotations

import pytest

from toppdblx.assign.l1 import (
    DEFAULT_PEG_ORGANIC_MAX_MW,
    DEFAULT_SALT_PRECIPITANT_MIN_MOLAR,
    L1_CLASSES,
    assign,
    family_of,
    to_molar,
)

PEG_MAX = DEFAULT_PEG_ORGANIC_MAX_MW
SALT_MIN = DEFAULT_SALT_PRECIPITANT_MIN_MOLAR


def component(**kwargs):
    base = {"chem_class": None, "concentration": None, "unit": None, "peg_mw": None,
            "cryo_evidence": None, "premix_id": None, "hofmeister_rank": None}
    base.update(kwargs)
    return base


def family(**kwargs):
    return family_of(component(**kwargs), PEG_MAX, SALT_MIN)


# --- the taxonomy itself --------------------------------------------------

def test_seven_classes_are_the_non_empty_subsets_of_three_families():
    """That is why there are seven, and it is worth asserting rather than trusting."""
    assert len(L1_CLASSES) == 7
    families = {"organic", "peg", "salt"}
    expected = {frozenset(s) for n in (1, 2, 3)
                for s in __import__("itertools").combinations(sorted(families), n)}
    assert set(L1_CLASSES) == expected


# --- unit conversion -----------------------------------------------------

def test_to_molar_converts_the_molar_family_only():
    assert to_molar(100, "millimolar") == pytest.approx(0.1)
    assert to_molar(1.5, "molar") == 1.5
    assert to_molar(20, "percent_w_v") is None


# --- PEG molecular weight boundary ---------------------------------------

@pytest.mark.parametrize("peg_mw,expected", [
    (200, "organic"), (400, "organic"), (600, "organic"),
    (1000, "peg"), (3350, "peg"), (8000, "peg"),
])
def test_low_molecular_weight_peg_counts_as_organic(peg_mw, expected):
    """Spec 6.3: PEG 400 behaves as an organic, PEG 8000 as a polymer."""
    assert family(chem_class="peg", peg_mw=peg_mw,
                  concentration=25, unit="percent_w_v") == expected


# --- the salt threshold --------------------------------------------------

def test_the_ion_concentration_of_a_peg_ion_screen_counts_as_a_salt():
    """0.2 M is what PEG/Ion formulates at: it is the co-precipitant, not an additive."""
    assert family(chem_class="salt", concentration=0.2, unit="molar") == "salt"
    assert family(chem_class="salt", concentration=200, unit="millimolar") == "salt"


def test_a_trace_salt_does_not_count():
    assert family(chem_class="salt", concentration=0.05, unit="molar") is None
    assert family(chem_class="salt", concentration=5, unit="millimolar") is None


def test_a_salt_with_no_concentration_cannot_be_judged():
    assert family(chem_class="salt") is None


# --- dual-role buffers ---------------------------------------------------

def test_a_buffer_scale_citrate_is_not_a_precipitant():
    """0.1 M sodium citrate is setting pH."""
    assert family(chem_class="buffer", hofmeister_rank=-3,
                  concentration=0.1, unit="molar") is None


def test_a_high_concentration_citrate_is_a_precipitating_salt():
    """0.8 M sodium citrate is precipitating, and excluding all buffers lost these."""
    assert family(chem_class="buffer", hofmeister_rank=-3,
                  concentration=0.8, unit="molar") == "salt"


def test_a_buffer_with_no_hofmeister_rank_never_counts():
    """Tris and HEPES have no anion of interest: they only ever set pH."""
    assert family(chem_class="buffer", hofmeister_rank=None,
                  concentration=2.0, unit="molar") is None


# --- exclusions ----------------------------------------------------------

def test_explicit_cryoprotectant_is_excluded():
    assert family(chem_class="polyol", cryo_evidence="explicit",
                  concentration=20, unit="percent_v_v") is None


def test_inferred_cryo_still_counts_as_a_precipitant():
    """Only explicit cryo is excluded: four in five cryo labels are inferences."""
    assert family(chem_class="polyol", cryo_evidence="inferred",
                  concentration=20, unit="percent_v_v") == "organic"


def test_unresolved_component_contributes_nothing():
    assert family(chem_class=None, concentration=20, unit="percent_w_v") is None


def test_additives_and_detergents_contribute_nothing():
    for chem_class in ("additive", "detergent", "other"):
        assert family(chem_class=chem_class, concentration=1.0, unit="molar") is None


def test_trace_percent_precipitant_does_not_count():
    """A 2% PEG is an additive; Top96 wells start around 5%."""
    assert family(chem_class="peg", peg_mw=3350, concentration=2, unit="percent_w_v") is None
    assert family(chem_class="peg", peg_mw=3350, concentration=20, unit="percent_w_v") == "peg"


# --- whole-condition assignment -----------------------------------------

def test_peg_plus_ion_condition_is_salt_peg():
    import polars as pl
    frame = pl.DataFrame([
        {"pdb_id": "1ABC", "crystal_id": "1", **component(
            chem_class="peg", peg_mw=3350, concentration=20, unit="percent_w_v")},
        {"pdb_id": "1ABC", "crystal_id": "1", **component(
            chem_class="salt", concentration=0.2, unit="molar")},
        {"pdb_id": "1ABC", "crystal_id": "1", **component(
            chem_class="buffer", concentration=0.1, unit="molar")},
    ])
    result = assign(frame, PEG_MAX, SALT_MIN)
    assert result["l1_precipitant_class"].item() == "Salt/PEG"


def test_condition_with_only_buffer_is_unassigned():
    import polars as pl
    frame = pl.DataFrame([
        {"pdb_id": "1ABC", "crystal_id": "1", **component(
            chem_class="buffer", concentration=0.1, unit="molar")},
    ])
    assert assign(frame, PEG_MAX, SALT_MIN)["l1_precipitant_class"].item() == "Unassigned"
