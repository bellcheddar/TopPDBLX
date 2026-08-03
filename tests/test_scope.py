"""Tests for the scope spans: which part of a deposition describes the condition.

The failure these prevent is silent in both directions. A missed protein section puts a storage
buffer into the dataset as chemistry the crystal grew in; a false one deletes a real condition
and leaves a record that says nothing. So the cases below fix the boundary from both sides, and
several of them exist because a first version got them wrong.
"""

from __future__ import annotations

import pytest

from toppdblx.parse import lexicon as lexicon_module, scope
from toppdblx.parse.rules import RuleParser


@pytest.fixture(scope="module")
def parser():
    return RuleParser(lexicon_module.load())


def roles(parser, text):
    record = parser.parse("TEST", "1", text)
    return {(c.role, c.name_canonical) for c in record.components
            if c.role != "not_a_component"}


# --- the spans themselves ----------------------------------------------------------------

def test_a_protein_section_needs_a_condition_section_after_it():
    """With no condition text to contrast against, the literal reading is the safe one: a
    deposition that describes only a buffer may simply be a terse condition."""
    assert scope.spans("PROTEIN SOLUTION: 10 MG/ML IN 25 MM MES, 100 MM NACL") == []


def test_a_protein_section_closes_at_the_condition_marker():
    text = "PROTEIN SOLUTION: 25 MM MES, 100 MM NACL. RESERVOIR: 20% PEG 4000"
    (start, end, role), = scope.spans(text)
    assert role == "protein_buffer"
    assert text[start:end].startswith("PROTEIN SOLUTION")
    assert "PEG" not in text[start:end]


def test_crystallised_from_is_growth_not_a_protein_buffer():
    """2,380 records write a perfectly ordinary condition this way. Matching bare `protein`
    would have swallowed the ammonium sulfate in every one of them."""
    assert scope.spans("PROTEIN WAS CRYSTALLIZED FROM 2M AMMONIUM SULFATE, PH 8.0") == []


def test_a_soak_runs_to_the_end_unless_growth_chemistry_reopens():
    plain = "0.1 M MES pH 6.5; crystals were soaked in 5 mM sucrose"
    (start, end, role), = scope.spans(plain)
    assert (role, end) == ("soak", len(plain))

    reopened = "crystals were soaked in 5 mM sucrose in the crystallization solution with 18% PEG"
    (start, end, role), = scope.spans(reopened)
    assert role == "soak"
    assert "PEG" not in reopened[start:end]


# --- the cursor --------------------------------------------------------------------------

def test_the_cursor_matches_a_repeated_clause_in_order():
    """Forward-only search. Pinning both occurrences of a repeated clause to the first would put
    a late condition clause inside an early protein span."""
    cursor = scope.Cursor("20% PEG, then later 20% PEG")
    first = cursor.locate("20% PEG")
    assert cursor.locate("20% PEG") > first


def test_an_unlocatable_clause_keeps_the_running_position():
    """The splitter tidies as it goes, so a clause is not always a literal substring. This must
    never raise: it is called on every clause of every record."""
    cursor = scope.Cursor("0.1 M tris")
    cursor.locate("0.1 M tris")
    assert cursor.locate("something the splitter invented") == len("0.1 M tris")


# --- end to end through the parser -------------------------------------------------------

def test_a_storage_buffer_is_kept_but_marked_out_of_scope(parser):
    """1BZS in shape. Every reagent is read exactly as before; only the role changes, so the
    reading is preserved and the classifier is told not to count it."""
    got = roles(parser, "PROTEIN SOLUTION: 10 MG/ML MMP8 IN 25 MM MES, 100 MM NACL, PH 6.0. "
                        "RESERVOIR: 20% PEG 4000, 0.1 M TRIS PH 8.5")
    assert ("protein_buffer", "MES") in got
    assert ("protein_buffer", "SODIUM_CHLORIDE") in got
    assert ("precipitant", "PEG_4000") in got
    assert ("buffer", "TRIS") in got


def test_an_ordinary_condition_is_untouched(parser):
    got = roles(parser, "20% PEG 3350, 0.2 M sodium chloride, 0.1 M Tris pH 8.5")
    assert got == {("precipitant", "PEG_3350"), ("salt", "SODIUM_CHLORIDE"), ("buffer", "TRIS")}


def test_the_condition_ph_is_not_the_storage_buffers(parser):
    """1,566 records state a pH inside a protein section and in 387 it is the only pH in the
    text. Taking it reports a condition pH that was never measured on the condition."""
    record = parser.parse("TEST", "1", "PROTEIN SOLUTION: 25 MM MES, PH 6.0. "
                                       "RESERVOIR: 20% PEG 4000, 0.1 M TRIS PH 8.5")
    assert record.ph == 8.5

    only = parser.parse("TEST", "1", "PROTEIN SOLUTION: 25 MM MES, PH 6.0. "
                                     "RESERVOIR: 20% PEG 4000")
    assert only.ph is None and only.ph_source == "unstated"


def test_an_unidentified_clause_in_a_span_stays_unknown(parser):
    """Marking it out-of-scope would move a genuine lexicon failure out of the identification
    denominator, so the corpus would score better for parsing worse."""
    record = parser.parse("TEST", "1", "PROTEIN SOLUTION: 10 mM zorbulase buffer. "
                                       "RESERVOIR: 20% PEG 4000")
    unidentified = [c for c in record.components if c.name_canonical is None
                    and c.role != "not_a_component"]
    assert all(c.role not in ("protein_buffer", "soak") for c in unidentified)


def test_a_scope_role_overrides_cryo(parser):
    """A glycerol in a storage buffer is not a cryoprotectant either, and leaving it as `cryo`
    would route it through the evidence-qualified exclusion instead of this one."""
    record = parser.parse("TEST", "1", "PROTEIN SOLUTION: 20 mM Tris, 10% glycerol. "
                                       "RESERVOIR: 20% PEG 4000")
    glycerol = [c for c in record.components if c.name_canonical == "GLYCEROL"]
    assert glycerol and glycerol[0].role == "protein_buffer"
    assert glycerol[0].cryo_evidence is None
