"""Tests for the reagent lexicon schema and the shipped ontology/synonyms.yaml.

The lexicon is the artefact every later stage keys off, so the failure modes that would be
silent are the ones tested: an alias claimed by two reagents, a premix pointing at a
canonical id that does not exist, and a PEG with no molecular weight.
"""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from toppdblx.parse import lexicon as lex


def make(**overrides):
    reagent = {
        "canonical_id": "TEST_REAGENT",
        "display_name": "Test reagent",
        "chem_class": "salt",
    }
    reagent.update(overrides)
    return reagent


# --- schema ---------------------------------------------------------------

def test_alias_claimed_by_two_reagents_is_a_hard_error():
    """Silent identification to whichever loaded first would be far worse than a crash."""
    with pytest.raises(ValidationError, match="claimed by more than one"):
        lex.Lexicon.model_validate({"version": "0.0.1", "reagents": [
            make(canonical_id="SALT_A", display_name="Salt A", aliases=["shared"]),
            make(canonical_id="SALT_B", display_name="Salt B", aliases=["Shared"]),
        ]})


def test_alias_collision_is_case_and_whitespace_insensitive():
    with pytest.raises(ValidationError, match="claimed by more than one"):
        lex.Lexicon.model_validate({"version": "0.0.1", "reagents": [
            make(canonical_id="SALT_A", display_name="Salt A", aliases=["Sodium  Chloride"]),
            make(canonical_id="SALT_B", display_name="sodium chloride"),
        ]})


def test_duplicate_canonical_id_is_rejected():
    with pytest.raises(ValidationError, match="duplicate canonical_id"):
        lex.Lexicon.model_validate({"version": "0.0.1", "reagents": [
            make(display_name="One"), make(display_name="Two"),
        ]})


def test_peg_without_molecular_weight_is_rejected():
    with pytest.raises(ValidationError, match="requires peg_mw"):
        lex.Lexicon.model_validate({"version": "0.0.1", "reagents": [
            make(canonical_id="PEG_X", display_name="PEG X", chem_class="peg"),
        ]})


def test_premix_pointing_at_an_unknown_id_is_rejected():
    with pytest.raises(ValidationError, match="unknown ids"):
        lex.Lexicon.model_validate({"version": "0.0.1", "reagents": [
            make(canonical_id="MIX", display_name="Mix", chem_class="premix",
                 premix_components=["DOES_NOT_EXIST"]),
        ]})


def test_lowercase_canonical_id_is_rejected():
    with pytest.raises(ValidationError, match="upper snake case"):
        lex.Lexicon.model_validate({"version": "0.0.1", "reagents": [
            make(canonical_id="lowercase_id"),
        ]})


def test_unknown_field_is_rejected():
    """extra='forbid' catches a typo in a field name, which would otherwise be ignored."""
    with pytest.raises(ValidationError):
        lex.Lexicon.model_validate({"version": "0.0.1", "reagents": [
            make(hoffmeister_rank=-4),          # misspelled
        ]})


# --- the shipped lexicon --------------------------------------------------

@pytest.fixture(scope="module")
def shipped():
    return lex.load()


def test_shipped_lexicon_validates(shipped):
    assert shipped.version
    assert len(shipped.reagents) > 100


def test_shipped_lexicon_identifies_real_corpus_spellings(shipped):
    index = shipped.index()
    cases = {
        "ammonium sulphate": "AMMONIUM_SULFATE",
        "(nh4)2so4": "AMMONIUM_SULFATE",
        "amso4": "AMMONIUM_SULFATE",
        "peg 3350": "PEG_3350",
        "peg 4k": "PEG_4000",
        "tris-hcl": "TRIS",
        "trishcl": "TRIS",
        "na-hepes": "HEPES",
        "hepes/naoh": "HEPES",
        "naoac": "SODIUM_ACETATE",
        "tri-sodium citrate": "SODIUM_CITRATE",
        "2-propanol": "ISOPROPANOL",
        "bme": "BETA_MERCAPTOETHANOL",
    }
    for spelling, expected in cases.items():
        assert index[spelling].canonical_id == expected, spelling


def test_bare_anions_identified_by_the_audit(shipped):
    """Settled in the 2026-07-29 audit: where a counter-ion is conventional, map it."""
    index = shipped.index()
    assert index["citrate"].canonical_id == "SODIUM_CITRATE"
    assert index["acetate"].canonical_id == "SODIUM_ACETATE"


def test_genuinely_ambiguous_strings_stay_unmapped(shipped):
    """The audit kept these unmapped, on two different grounds.

    A bare PEG is missing its molecular weight, which is part of the reagent's identity
    rather than a counter-ion convention. A bare phosphate genuinely varies between sodium,
    potassium and mixed Na/K, and the choice changes the chemistry.
    """
    index = shipped.index()
    for bare in ("peg", "polyethylene glycol", "peg mme", "phosphate"):
        assert bare not in index, f"{bare!r} should stay unmapped"


def test_every_peg_has_a_molecular_weight_and_a_unit(shipped):
    for reagent in shipped.reagents:
        if reagent.chem_class == "peg":
            assert reagent.peg_mw, reagent.canonical_id
            assert reagent.default_unit, reagent.canonical_id


def test_low_molecular_weight_pegs_default_to_volume_percent(shipped):
    """Below ~600 a PEG behaves as an organic and is reported v/v (spec 6.3)."""
    for reagent in shipped.reagents:
        if reagent.chem_class == "peg" and not reagent.is_mme:
            expected = "percent_v_v" if reagent.peg_mw <= 600 else "percent_w_v"
            assert reagent.default_unit == expected, reagent.canonical_id


def test_buffers_carry_a_pka(shipped):
    missing = [r.canonical_id for r in shipped.reagents
               if r.chem_class == "buffer" and r.buffer_pka is None]
    assert not missing, f"buffers without a pKa: {missing}"


def test_hofmeister_ranks_order_the_series_correctly(shipped):
    by_id = shipped.by_id()
    sulfate = by_id["AMMONIUM_SULFATE"].hofmeister_rank
    chloride = by_id["SODIUM_CHLORIDE"].hofmeister_rank
    thiocyanate = by_id["POTASSIUM_THIOCYANATE"].hofmeister_rank
    assert sulfate < chloride < thiocyanate
    assert chloride == 0


def test_yaml_file_parses_as_plain_yaml():
    """Guards against a tab or an unquoted colon breaking the file for other tools."""
    data = yaml.safe_load(lex.LEXICON_PATH.read_text())
    assert set(data) == {"version", "reagents"}


def test_reported_name_count_is_the_parser_lookup_index(shipped):
    """"527 reagents, 1,293 names" in the README and changelog means the *normalised lookup index*,
    not the raw alias count nor aliases plus display names.

    Pinned because the three counts differ by up to 25% and the changelog was published with the
    wrong one. The index is the honest figure: it is what the parser can actually match, after
    tidying collapses hydration state and punctuation, so two aliases that normalise to the same
    string are one name, not two.
    """
    index = shipped.index()
    assert len(index) == 1293, f"lookup index is {len(index)}; update the README and CHANGELOG"
    assert len(shipped.reagents) == 527
    raw_aliases = sum(len(r.aliases) for r in shipped.reagents)
    assert raw_aliases != len(index), "the two counts must not be conflated"
