"""Tests for the deterministic rule parser.

Every condition string here is real, taken from the corpus during development. The cases
that earned their place are the ones that were silently wrong before a test existed:
multi-line depositions, `in` and double-space separators, leading conjunctions, and
molecular weights being eaten as if they were concentrations.
"""

from __future__ import annotations

import pytest

from crystal_ball.parse.lexicon import load
from crystal_ball.parse.rules import RuleParser


@pytest.fixture(scope="module")
def parser():
    return RuleParser(load())


def parse(parser, text, **kwargs):
    return parser.parse("TEST", "1", text, **kwargs)


def canonical(record):
    return [c.name_canonical for c in record.components]


# --- separators -----------------------------------------------------------

def test_newline_separated_components_all_parse(parser):
    """Depositors put one component per line. Flattening first loses the lot."""
    record = parse(parser, "1.7 to 2.1 ammonium sulfate\n0.1M MES (pH 6 to 7)\n"
                           "5 to 15%(v/v) glycerol\n7 to 12%(v/v) 1,4-dioxane")
    assert canonical(record) == ["AMMONIUM_SULFATE", "MES", "GLYCEROL", "DIOXANE"]
    assert record.discard_reason is None


def test_double_space_separates_components(parser):
    record = parse(parser, "20 % w/v Polyethylene glycol 6,000  100 mM tri-Sodium citrate; pH 5.0")
    assert canonical(record) == ["PEG_6000", "SODIUM_CITRATE"]


def test_in_separates_components_before_a_concentration(parser):
    record = parse(parser, "50mM CaCl2 in 0.1M Cacodylate")
    assert canonical(record) == ["CALCIUM_CHLORIDE", "SODIUM_CACODYLATE"]


def test_leading_conjunction_does_not_block_lookup(parser):
    """", and 172 mM ammonium nitrate" must still resolve the reagent."""
    record = parse(parser, "5 mM MnCl2 in 20 % PEG 3350, and 172 mM ammonium nitrate")
    assert canonical(record) == ["MANGANESE_CHLORIDE", "PEG_3350", "AMMONIUM_NITRATE"]


def test_thousands_separator_does_not_split_a_reagent(parser):
    record = parse(parser, "20 % w/v Polyethylene glycol 6,000")
    assert canonical(record) == ["PEG_6000"]


# --- concentrations and units ---------------------------------------------

def test_range_keeps_endpoints_and_uses_the_midpoint(parser):
    record = parse(parser, "18-22% PEG 3350")
    component = record.components[0]
    assert component.concentration_is_range
    assert component.concentration_range == (18.0, 22.0)
    assert component.concentration == 20.0


def test_inferred_units_are_flagged_and_explicit_ones_are_not(parser):
    record = parse(parser, "10% PEG3350, 20 % w/v PEG 8000")
    assert record.components[0].unit_inferred is True     # bare %
    assert record.components[1].unit_inferred is False    # explicit w/v
    assert "unit_inferred" in record.provenance.flags


def test_molecular_weight_survives_when_no_concentration_is_stated(parser):
    """"peg 8000" alone: the number is identity, not amount."""
    record = parse(parser, "0.1 M HEPES, PEG 8000")
    assert "PEG_8000" in canonical(record)


# --- pH attribution -------------------------------------------------------

def test_ph_attached_to_a_buffer_is_the_buffer_ph(parser):
    record = parse(parser, "0.1M Sodium acetate trihydrate (pH 4.6), 1.0M Ammonium sulfate")
    assert record.ph == 4.6
    assert record.ph_source == "buffer"


def test_standalone_ph_after_a_buffer_clause_is_attributed_to_it(parser):
    record = parse(parser, "18% PEG 8000, 50mM CaCl2 in 0.1M Cacodylate, pH 6.95")
    assert record.ph == 6.95
    assert record.ph_source == "buffer"


def test_explicitly_final_ph_is_marked_final(parser):
    record = parse(parser, "20% PEG 3350, 0.1 M HEPES, final pH 7.5")
    assert record.ph_source == "final"


def test_standalone_ph_with_no_buffer_is_unstated_not_assumed(parser):
    record = parse(parser, "2.0 M ammonium sulfate, pH 7.0")
    assert record.ph == 7.0
    assert record.ph_source == "unstated"


def test_ph_range_is_preserved(parser):
    record = parse(parser, "0.1M MES (pH 6 to 7), 20% PEG 3350")
    assert record.ph_is_range
    assert record.ph_range == (6.0, 7.0)


def test_conflict_with_the_reported_ph_is_flagged(parser):
    record = parse(parser, "0.1 M HEPES pH 7.5, 20% PEG 3350", ph_reported=5.0)
    assert "ph_conflicts_with_reported" in record.provenance.flags


# --- temperature ----------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("20% PEG 3350, temperature 293K", 293.0),
    ("20% PEG 3350, 277K", 277.0),
    ("20% PEG 3350, 20 C", pytest.approx(293.15)),
    ("20% PEG 3350, room temperature", 293.0),
])
def test_temperature_from_text(parser, text, expected):
    assert parse(parser, text).temperature_k == expected


def test_reported_temperature_wins_but_a_clash_is_flagged(parser):
    record = parse(parser, "20% PEG 3350, temperature 277K", temp_k_reported=298.0)
    assert record.temperature_k == 298.0
    assert record.temperature_source == "conflict"
    assert "temperature_conflicts_with_reported" in record.provenance.flags


def test_agreement_with_the_reported_temperature_is_recorded(parser):
    record = parse(parser, "20% PEG 3350, temperature 293K", temp_k_reported=293.0)
    assert record.temperature_source == "both_agree"


# --- cryoprotectants ------------------------------------------------------

def test_explicit_cryoprotectant_is_marked_explicit(parser):
    record = parse(parser, "30% W/V PEG 4000, 0.1 M sodium citrate pH 5.6, "
                           "for cryoprotection 10% of PEG 400 was added")
    evidence = [c.cryo_evidence for c in record.components if c.cryo_evidence]
    assert "explicit" in evidence


def test_low_concentration_glycerol_is_inferred_cryo(parser):
    record = parse(parser, "18% PEG 8000, 5% Glycerol, 0.1M cacodylate")
    glycerol = next(c for c in record.components if c.name_canonical == "GLYCEROL")
    assert glycerol.cryo_evidence == "inferred"
    assert glycerol.role == "cryo"


def test_high_concentration_glycerol_is_a_precipitant_not_cryo(parser):
    """A third of glycerol values exceed 15%. Those are doing precipitant work."""
    record = parse(parser, "25% Glycerol, 0.1M HEPES")
    glycerol = next(c for c in record.components if c.name_canonical == "GLYCEROL")
    assert glycerol.cryo_evidence is None
    assert glycerol.role == "precipitant"


# --- roles ----------------------------------------------------------------

def test_roles_follow_chemical_class(parser):
    record = parse(parser, "20% PEG 3350, 0.2 M NaCl, 0.1 M HEPES, 1 mM DTT")
    roles = {c.name_canonical: c.role for c in record.components}
    assert roles["PEG_3350"] == "precipitant"
    assert roles["SODIUM_CHLORIDE"] == "salt"
    assert roles["HEPES"] == "buffer"
    assert roles["DTT"] == "additive"


def test_buffer_premix_is_given_a_buffer_role(parser):
    record = parse(parser, "0.1 M MMT buffer, 25% PEG 3350")
    mmt = next(c for c in record.components if c.name_canonical == "MMT_BUFFER")
    assert mmt.role == "buffer"
    assert mmt.premix_id == "MMT_BUFFER"


# --- discards -------------------------------------------------------------

@pytest.mark.parametrize("text,reason", [
    (None, "EMPTY"),
    ("", "EMPTY"),
    ("   ", "EMPTY"),
    ("pH 7", "TOO_SHORT"),
    ("VAPOR DIFFUSION, HANGING DROP, temperature 293K", "METHOD_ONLY"),
    ("see also PMID 12345678 for details", "REFERENCE_ONLY"),
])
def test_discard_reasons(parser, text, reason):
    assert parse(parser, text).discard_reason == reason


def test_unresolvable_reagents_are_discarded_as_no_match(parser):
    record = parse(parser, "0.2 M frobnicase reagent, 5 mM widgetol solution")
    assert record.discard_reason == "NO_REAGENT_MATCH"


def test_a_good_record_is_kept_with_full_confidence(parser):
    record = parse(parser, "20% PEG 3350, 0.2 M NaCl, 0.1 M HEPES pH 7.5")
    assert record.discard_reason is None
    assert record.provenance.parse_confidence == 1.0
    assert record.is_usable


# --- opportunistic capture ------------------------------------------------

def test_protein_concentration_and_drop_ratio_are_captured_when_present(parser):
    record = parse(parser, "20% PEG 3350, 0.1M HEPES, protein concentration 15 mg/ml, "
                           "mixed in a 1:1 ratio with the well solution")
    assert record.protein_concentration_mg_ml == 15.0
    assert record.drop_ratio == "1:1"
