"""Tests for the second curation round's queue builder.

The point of merging two sources is to spend an expert's attention once per *reagent* rather than
once per *spelling*. These assert that the merge actually achieves that, and that nothing already
settled comes back.
"""

from __future__ import annotations

import pytest

from toppdblx.parse.curation_queue import humanise
from toppdblx.parse.lexicon_questions import normalise_for_match


def test_a_model_proposal_reads_as_corpus_text():
    """The model emits canonical ids; the corpus holds prose. They have to meet somewhere for the
    merge to find that `MANGANESE_SULFATE` and `manganese sulphate` are one decision."""
    assert humanise("MANGANESE_SULFATE") == "manganese sulfate"
    assert humanise("PEG_1450") == "peg 1450"


def test_spellings_of_one_reagent_normalise_together():
    """`l-arginine` and `l arginine` are one decision, not two. This is the whole reason the
    model's pre-normalised names are worth more per question than raw corpus strings."""
    assert (normalise_for_match("l-arginine") == normalise_for_match("l arginine"))


def test_hydration_state_does_not_split_a_decision():
    assert (normalise_for_match("manganese sulfate monohydrate")
            == normalise_for_match("manganese sulfate"))


@pytest.mark.parametrize("proposed,surface", [
    ("MANGANESE_SULFATE", "manganese sulfate"),
    ("ARGININE", "arginine"),
    ("GUANIDINE", "guanidine"),
])
def test_a_model_name_matches_its_corpus_surface_form(proposed, surface):
    """The merge claims a raw string for a model proposal when they normalise alike. If this
    broke, the same reagent would be asked about twice and the weights would be split."""
    assert normalise_for_match(humanise(proposed)) == normalise_for_match(surface)


def test_peg_molecular_weights_stay_separate_decisions():
    """PEG 1450 and PEG 350 are different reagents. The merge must not collapse them, or one
    answer would silently set the molecular weight for the other."""
    assert (normalise_for_match(humanise("PEG_1450"))
            != normalise_for_match(humanise("PEG_350")))
