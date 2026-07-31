"""Tests for the screen library and the matching fingerprint.

The fingerprint rule encodes decision 12.3 and is easy to get subtly wrong in a way that
silently corrupts the validation set, so each half of it is pinned down here.
"""

from __future__ import annotations

import pytest

from toppdblx.assign import screens
from toppdblx.parse.lexicon import load as load_lexicon
from toppdblx.parse.rules import RuleParser
from toppdblx.parse.schema import Component


def component(name, concentration=None, unit=None, cryo=None, chem_class=None):
    return Component(name_raw=name.lower(), name_canonical=name, chem_class=chem_class,
                     concentration=concentration, unit=unit, cryo_evidence=cryo)


# --- fingerprint ----------------------------------------------------------

def test_explicit_cryo_is_excluded_from_the_fingerprint():
    """An explicitly declared cryoprotectant is added after the drop, not part of it."""
    parts = [component("PEG_3350", 20, "percent_w_v"),
             component("GLYCEROL", 10, "percent_v_v", cryo="explicit")]
    assert screens.fingerprint(parts) == frozenset({"PEG_3350"})


def test_inferred_cryo_stays_in_the_fingerprint():
    """A third of glycerol values are precipitant scale; dropping inferred cryo would
    corrupt otherwise correct matches."""
    parts = [component("PEG_3350", 20, "percent_w_v"),
             component("GLYCEROL", 5, "percent_v_v", cryo="inferred")]
    assert screens.fingerprint(parts) == frozenset({"PEG_3350", "GLYCEROL"})


def test_unidentified_components_block_an_exact_match():
    """A record with an unknown reagent must not match a well as though it were absent."""
    parts = [component("PEG_3350", 20, "percent_w_v"),
             Component(name_raw="frobnicase", name_canonical=None)]
    printed = screens.fingerprint(parts)
    assert printed != frozenset({"PEG_3350"})
    assert any(name.startswith("?") for name in printed)


# --- concentration comparison ---------------------------------------------

def test_molar_and_millimolar_are_compared_after_conversion():
    """0.1 M and 100 mM are the same concentration written two ways."""
    assert screens.concentrations_agree(
        component("TRIS", 0.1, "molar"), component("TRIS", 100.0, "millimolar"))


def test_micromolar_converts_too():
    assert screens.concentrations_agree(
        component("ZINC_CHLORIDE", 0.001, "molar"),
        component("ZINC_CHLORIDE", 1000.0, "micromolar"))


def test_weight_volume_and_volume_volume_are_never_interchangeable():
    """30% v/v of an organic is not 30% w/v of a polymer."""
    assert not screens.concentrations_agree(
        component("PEG_400", 30.0, "percent_v_v"),
        component("PEG_400", 30.0, "percent_w_v"))


def test_percent_uses_an_absolute_tolerance():
    assert screens.concentrations_agree(
        component("PEG_3350", 20.0, "percent_w_v"),
        component("PEG_3350", 21.5, "percent_w_v"))
    assert not screens.concentrations_agree(
        component("PEG_3350", 20.0, "percent_w_v"),
        component("PEG_3350", 25.0, "percent_w_v"))


def test_molar_uses_a_relative_tolerance():
    """Spec 6.3: 0.8 M against 2.4 M ammonium sulfate is a different regime entirely."""
    assert screens.concentrations_agree(
        component("AMMONIUM_SULFATE", 2.0, "molar"),
        component("AMMONIUM_SULFATE", 2.2, "molar"))
    assert not screens.concentrations_agree(
        component("AMMONIUM_SULFATE", 0.8, "molar"),
        component("AMMONIUM_SULFATE", 2.4, "molar"))


def test_mg_per_ml_and_g_per_litre_are_the_same_scale():
    assert screens.concentrations_agree(
        component("X", 5.0, "mg_ml"), component("X", 5.0, "g_l"))


def test_missing_concentrations_compare_equal_only_to_each_other():
    assert screens.concentrations_agree(component("X"), component("X"))
    assert not screens.concentrations_agree(component("X"), component("X", 1.0, "molar"))


# --- the shipped library --------------------------------------------------

@pytest.fixture(scope="module")
def library():
    return screens.load(RuleParser(load_lexicon()))


def test_every_shipped_well_parses_completely(library):
    """The vendor strings are clean prose. A parser that stumbles here has no business on
    the messy corpus, so this is a direct check on the parser, not just on the library."""
    unidentified = [(w.catalogue, w.well, c.name_raw)
                  for w in library.wells for c in w.components if not c.name_canonical]
    assert not unidentified, f"unidentified reagents in screen wells: {unidentified[:10]}"


def test_library_covers_the_expected_screens(library):
    assert len(library.wells) > 400
    assert len({w.catalogue for w in library.wells}) >= 9
    names = {w.screen for w in library.wells}
    assert any("Crystal Screen" in n for n in names)


def test_a_known_well_round_trips_to_its_components(library):
    """Crystal Screen 32 is a single-salt condition and must parse to exactly that."""
    well = next(w for w in library.wells
                if w.catalogue == "HR2-110" and w.well == "32")
    assert well.condition_text.strip() == "2.0 M Ammonium sulfate"
    assert [c.name_canonical for c in well.components] == ["AMMONIUM_SULFATE"]
    assert well.components[0].concentration == 2.0
    assert well.components[0].unit == "molar"


def test_wells_have_stable_fingerprints(library):
    """Every identified well must produce a non-empty fingerprint, or it can never match."""
    empty = [(w.catalogue, w.well) for w in library.wells if not w.fingerprint]
    assert not empty
