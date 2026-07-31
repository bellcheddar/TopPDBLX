"""Tests for the R1 text-to-JSON parser's data builder and scoring.

The scoring rules are the whole of R1's evidence, so they are asserted as claims about what
counts as a correct parse rather than as code behaviour. Two failure modes matter most:

  *scoring too generously*, which would let R1 pass its 85% identification gate on output that is
  not actually usable downstream;

  *conflating fidelity with a win*, which is the circularity trap: labels come from the rule
  parser, so imitating it perfectly proves nothing about beating it.
"""

from __future__ import annotations

import json
import pytest

from toppdblx.models.build_slm_dataset import target_json
from toppdblx.models.eval_slm import check, component_key, wilson

LEXICON = {"PEG_3350", "SODIUM_CHLORIDE", "MOPS", "MAGNESIUM_SULFATE"}


def comp(role="salt", name="SODIUM_CHLORIDE", concentration=0.2, unit="molar"):
    return {"role": role, "name_canonical": name, "concentration": concentration, "unit": unit}


# --- the training target ------------------------------------------------------------------

def test_target_carries_only_what_is_readable_from_the_text():
    """Curated chemistry is deliberately excluded from the target.

    PEG molecular weight, Hofmeister rank and buffer pKa come from the lexicon, not from the
    condition string. Asking the model to emit them invites it to invent chemistry it cannot
    see, so the target is restricted to the four fields the text actually contains.
    """
    row = comp()
    row.update({"peg_mw": 3350, "hofmeister_rank": -4, "chem_class": "salt"})
    emitted = json.loads(target_json([row]))[0]
    assert set(emitted) == {"role", "name", "amount", "unit"}


def test_missing_amount_survives_as_null_rather_than_being_invented():
    """Roughly a fifth of usable records name a precipitant but never state how much. The target
    must preserve that absence: a model taught to guess a number here would be fabricating."""
    emitted = json.loads(target_json([comp(concentration=None, unit=None)]))[0]
    assert emitted["amount"] is None and emitted["unit"] is None


# --- schema validity ---------------------------------------------------------------------

def test_unparseable_output_is_invalid():
    assert check("[{'role': 'salt'", LEXICON)["valid"] is False


def test_truncated_generation_is_invalid_not_partially_credited():
    """A generation cut off by the token limit must score as invalid. Giving partial credit for
    the components that did emit would hide truncation, which is the failure mode that makes the
    output unusable rather than merely incomplete."""
    assert check('[{"role":"salt","name":"SODIUM_CHLORIDE","amount":0.2,"unit":"mol',
                 LEXICON)["valid"] is False


def test_role_outside_the_closed_vocabulary_is_invalid():
    """Roles are a closed set. An invented role would break the ontology join downstream, so it
    is a schema failure rather than a identification failure."""
    assert check('[{"role":"precipitant_maybe","name":"PEG_3350","amount":20,'
                 '"unit":"percent_w_v"}]', LEXICON)["valid"] is False


def test_unit_outside_the_closed_vocabulary_is_invalid():
    """The example is deliberately not "M": that is a real chemist's spelling of molar and is now
    repaired rather than rejected. What must still fail is a unit that means nothing."""
    assert check('[{"role":"salt","name":"SODIUM_CHLORIDE","amount":0.2,"unit":"parsecs"}]',
                 LEXICON)["valid"] is False


def test_null_unit_is_valid_because_unstated_amounts_are_real():
    scored = check('[{"role":"precipitant","name":"PEG_3350","amount":null,"unit":null}]',
                   LEXICON)
    assert scored["valid"] and scored["n_identified"] == 1


def test_empty_array_is_valid_but_identifies_nothing():
    """An honest "I found no components" is schema-valid. It must not count towards identification,
    or a model that always emitted [] would score 100% by parsing nothing."""
    scored = check("[]", LEXICON)
    assert scored["valid"] and scored["n"] == 0 and scored["n_identified"] == 0


# --- identification --------------------------------------------------------------------------

def test_identification_requires_a_name_the_lexicon_knows():
    """The gate is whether the emitted name joins to curated chemistry. A plausible-looking but
    unknown name is not identified, however well-formed the JSON around it is."""
    scored = check('[{"role":"salt","name":"SODIUM_CHLORIDE","amount":0.2,"unit":"molar"},'
                   '{"role":"additive","name":"6_AMINOHEXANOIC_ACID","amount":5,'
                   '"unit":"percent_w_v"}]', LEXICON)
    assert scored["valid"] and scored["n"] == 2 and scored["n_identified"] == 1


def test_degenerate_repetition_does_not_identify():
    """An undertrained model emits names like SODIUM_SODIUM_CHLORIDE and MME_MME_MME. These are
    schema-valid and must still score as unidentified, which is what makes the metric able to
    detect an undertrained adapter at all."""
    scored = check('[{"role":"salt","name":"SODIUM_SODIUM_CHLORIDE","amount":0.2,'
                   '"unit":"molar"}]', LEXICON)
    assert scored["valid"] and scored["n_identified"] == 0


# --- fidelity scoring --------------------------------------------------------------------

def test_component_identity_ignores_float_noise():
    """0.1 reproduced as 0.1000000001 is not a parsing error."""
    assert (component_key({"role": "salt", "name": "SODIUM_CHLORIDE",
                           "amount": 0.1000000001, "unit": "molar"})
            == component_key({"role": "salt", "name": "SODIUM_CHLORIDE",
                              "amount": 0.1, "unit": "molar"}))


def test_component_identity_separates_unit_families():
    """0.1 molar and 0.1 millimorlar are a thousandfold apart. The key must not treat the
    amount alone as identity, or a unit error would score as a match."""
    assert (component_key({"role": "salt", "name": "SODIUM_CHLORIDE",
                           "amount": 0.1, "unit": "molar"})
            != component_key({"role": "salt", "name": "SODIUM_CHLORIDE",
                              "amount": 0.1, "unit": "millimolar"}))


def test_component_identity_distinguishes_role():
    """The same reagent can be a salt or an additive depending on context, and the ontology
    treats those differently, so role is part of identity."""
    assert (component_key({"role": "salt", "name": "MOPS", "amount": 0.1, "unit": "molar"})
            != component_key({"role": "buffer", "name": "MOPS", "amount": 0.1,
                              "unit": "molar"}))


# --- the not_a_component class ------------------------------------------------------------

def test_non_component_is_excluded_from_the_identification_denominator():
    """A model saying "this text names no reagent" is right, and no lexicon entry could have
    matched it. Counting it as a miss would measure an artefact rather than the model, which is
    the same correction applied to the rule parser."""
    scored = check('[{"role":"not_a_component","name":null,"amount":null,"unit":null},'
                   '{"role":"salt","name":"SODIUM_CHLORIDE","amount":0.2,"unit":"molar"}]',
                   LEXICON)
    assert scored["valid"]
    assert scored["n"] == 1 and scored["n_identified"] == 1
    assert scored["n_non_components"] == 1


def test_an_all_non_component_answer_identifies_nothing_and_claims_nothing():
    """Method-only text should yield no components at all. This must not read as 100%
    identification (1 of 1) nor as 0% (0 of 1): there is simply nothing in the denominator."""
    scored = check('[{"role":"not_a_component","name":null,"amount":null,"unit":null}]', LEXICON)
    assert scored["valid"] and scored["n"] == 0 and scored["n_identified"] == 0


def test_accounted_for_accepts_a_confident_non_reagent_verdict():
    """Records containing method text must remain usable as training examples, or the model never
    sees the output it needs for 13.4% of the residual and learns to invent a reagent instead."""
    from toppdblx.models.build_slm_dataset import accounted_for
    assert accounted_for({"name_canonical": None, "role": "not_a_component"}) is True
    assert accounted_for({"name_canonical": None, "role": "unknown"}) is False
    assert accounted_for({"name_canonical": "PEG_3350", "role": "precipitant"}) is True


# --- confidence intervals on the gates ----------------------------------------------------

def test_wilson_interval_brackets_the_point_estimate():
    from toppdblx.models.eval_slm import wilson
    lo, hi = wilson(870, 1000)
    assert lo < 87.0 < hi


def test_wilson_interval_never_exceeds_one_hundred_percent():
    """Near a proportion of 1 the normal approximation runs past 100%, which is why Wilson is
    used: a identification rate cannot exceed 100% and an interval claiming so is not reportable."""
    lo, hi = wilson(800, 800)
    assert hi <= 100.0 and lo < 100.0


def test_wilson_interval_widens_as_the_sample_shrinks():
    """The whole reason for reporting intervals: an 800-record sweep cannot distinguish 87% from
    85%, and quoting the point estimates alone invited exactly that wrong conclusion."""
    from toppdblx.models.eval_slm import wilson
    narrow = wilson(8700, 10000)
    wide = wilson(87, 100)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


def test_an_empty_sample_yields_no_interval_rather_than_dividing_by_zero():
    from toppdblx.models.eval_slm import wilson
    assert wilson(0, 0) == (0.0, 0.0)


# --- run naming --------------------------------------------------------------------------

def test_model_slug_strips_vendor_and_precision_suffixes():
    from toppdblx.models.train_slm import model_slug
    assert model_slug("mlx-community/SmolLM2-360M-Instruct") == "smollm2-360m"
    assert model_slug("mlx-community/SmolLM2-360M-Instruct-bf16") == "smollm2-360m"


def test_run_numbering_starts_at_one_and_increments(tmp_path):
    """mlx-lm takes the W&B run name from the adapter directory's basename and offers no
    override, so the directory carries the run's identity. Numbering is read from disk rather
    than from a counter so it cannot drift from what actually exists."""
    from toppdblx.models.train_slm import next_run_name
    model = "mlx-community/SmolLM2-360M-Instruct"
    assert next_run_name(tmp_path, model).endswith("round01")
    (tmp_path / next_run_name(tmp_path, model)).mkdir()
    assert next_run_name(tmp_path, model).endswith("round02")


def test_run_numbering_ignores_unrelated_directories(tmp_path):
    from toppdblx.models.train_slm import next_run_name
    model = "mlx-community/SmolLM2-360M-Instruct"
    (tmp_path / "adapters").mkdir()
    (tmp_path / "sweep").mkdir()
    assert next_run_name(tmp_path, model).endswith("round01")


def test_run_numbering_continues_past_a_deleted_round(tmp_path):
    """Deleting round02 must not cause round03 to be reissued and overwrite it in W&B."""
    from toppdblx.models.train_slm import next_run_name
    model = "mlx-community/SmolLM2-360M-Instruct"
    for n in (1, 3):
        (tmp_path / f"r1-parse-residual-smollm2-360m-round{n:02d}").mkdir()
    assert next_run_name(tmp_path, model).endswith("round04")


# --- logging cadence ----------------------------------------------------------------------

def test_eval_cadence_must_be_a_multiple_of_report_cadence():
    """mlx-lm writes to W&B only inside its two report callbacks, so these flags alone set plot
    density. They must not be equal (that gave six points across 1,200 iterations) but the eval
    cadence must stay a whole multiple of the report cadence, or validation points fall between
    training points and the curves cannot be read against each other."""
    from toppdblx.models.train_slm import main
    with pytest.raises(SystemExit, match="multiple of"):
        main(["--steps-per-eval", "100", "--steps-per-report", "30", "--no-wandb"])


def test_default_cadences_align_and_are_dense_enough(monkeypatch):
    """Checks the defaults actually in force, not the source text that sets them.

    Two claims: the cadences align (eval is a whole multiple of report), and they are dense
    enough to show the part of the run that matters. On round 02, 98% of the validation-loss
    collapse happened before iteration 200, so a cadence of 100 would have put one point inside
    it. At 50 a 1,200-iteration run yields 24 validation and 120 training points.
    """
    import argparse
    from toppdblx.models import train_slm

    captured = {}
    original = argparse.ArgumentParser.parse_args

    def capture(self, args=None, namespace=None):
        parsed = original(self, args, namespace)
        captured.update(vars(parsed))
        raise SystemExit("stop before training")

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture)
    with pytest.raises(SystemExit):
        train_slm.main([])

    report, evaluate = captured["steps_per_report"], captured["steps_per_eval"]
    assert evaluate % report == 0, "curves would not align"
    assert 1200 // evaluate >= 20, "too few validation points to see the early collapse"
    assert 1200 // report >= 100, "too few training points for a readable curve"


# --- unit normalisation -------------------------------------------------------------------

@pytest.mark.parametrize("written,meant", [
    ("mM", "millimolar"), ("mm", "millimolar"), ("mmol/L", "millimolar"),
    ("M", "molar"), ("mol/L", "molar"),
    ("% w/v", "percent_w_v"), ("w/v", "percent_w_v"),
    ("% v/v", "percent_v_v"), ("v/v", "percent_v_v"),
    ("mg/ml", "mg_ml"),
])
def test_chemist_spellings_of_units_are_repaired_not_rejected(written, meant):
    """`bad_unit` was 61% of all remaining invalid output, and every case was the model writing a
    chemist's spelling of a unit it had learned correctly. Rejecting those scores a right answer
    as malformed JSON."""
    from toppdblx.models.eval_slm import normalise_unit
    assert normalise_unit(written) == meant


def test_a_repaired_unit_makes_the_record_valid():
    scored = check('[{"role":"salt","name":"SODIUM_CHLORIDE","amount":200,"unit":"mM"}]', LEXICON)
    assert scored["valid"] and scored["n_identified"] == 1
    assert scored["parsed"][0]["unit"] == "millimolar", "the repair must reach the output"


def test_a_bare_percentage_is_never_guessed():
    """Whether `%` means w/v or v/v depends on the chemistry, so mapping it would invent data.
    That decision belongs to parse.quantity.infer_unit, which has the reagent class to hand."""
    from toppdblx.models.eval_slm import normalise_unit
    assert normalise_unit("%") == "%"
    assert check('[{"role":"precipitant","name":"PEG_3350","amount":20,"unit":"%"}]',
                 LEXICON)["valid"] is False


def test_an_invented_unit_is_still_rejected():
    from toppdblx.models.eval_slm import normalise_unit
    assert normalise_unit("furlongs") == "furlongs"
    assert normalise_unit(None) is None


# --- groundedness: is the named reagent actually in the text? ------------------------------

ALIASES = {"PEG_3350": ["peg3350", "polyethyleneglycol3350"],
           "SODIUM_CHLORIDE": ["sodiumchloride", "nacl"],
           "MPD": ["mpd", "2methyl24pentanediol", "methylpentanediol"]}


def test_a_reagent_the_text_mentions_is_grounded():
    from toppdblx.models.eval_slm import grounded_in_text
    assert grounded_in_text("PEG_3350", "20% w/v PEG 3350, 0.1 M HEPES", ALIASES) is True


def test_a_reagent_the_text_does_not_mention_is_not_grounded():
    """The whole point. Identification alone scores this as correct, because SODIUM_CHLORIDE is a
    real lexicon entry: it is simply not the reagent in front of the model. Curation exists to
    make names mean something, so a metric that cannot tell a real name from the right one is
    measuring nothing worth having."""
    from toppdblx.models.eval_slm import grounded_in_text
    assert grounded_in_text("SODIUM_CHLORIDE", "20% w/v PEG 3350, 0.1 M HEPES", ALIASES) is False


@pytest.mark.parametrize("text", ["25% peg-3350", "25% PEG3350", "25% peg 3,350"])
def test_grounding_ignores_spacing_and_punctuation(text):
    """Punctuation is exactly what varies between spellings, so it is stripped on both sides."""
    from toppdblx.models.eval_slm import grounded_in_text
    assert grounded_in_text("PEG_3350", text, ALIASES) is True


def test_grounding_matches_any_known_spelling():
    from toppdblx.models.eval_slm import grounded_in_text
    assert grounded_in_text("MPD", "30% 2-methyl-2,4-pentanediol", ALIASES) is True


def test_an_unnamed_component_is_never_grounded():
    from toppdblx.models.eval_slm import grounded_in_text
    assert grounded_in_text(None, "anything", ALIASES) is False


# --- the grounding guard at inference -------------------------------------------------------

@pytest.mark.parametrize("name,text", [
    ("AMMONIUM_ACETATE", "50 MM AMMONIUN ACETATE, 50 MM HEPES"),   # depositor typo
    ("TACSIMATE", "20% PEG 5000 MME, 5% Tasimate pH7.0"),          # depositor typo
])
def test_a_typo_the_model_read_through_is_kept(name, text):
    """The one thing the model does that no rule or lexicon alias ever could. "AMMONIUN" and
    "Tasimate" are one-off deposition errors: no alias will ever cover them, and discarding
    these would throw away the model's only demonstrated advantage."""
    from toppdblx.models.apply_slm import GROUNDING_FUZZY_MIN, _best_similarity
    from toppdblx.models.eval_slm import load_aliases
    assert _best_similarity(name, text, load_aliases()) >= GROUNDING_FUZZY_MIN


@pytest.mark.parametrize("name,text", [
    ("TRIS", "pH 3.3"),
    ("SODIUM_DIHYDROGEN_PHOSPHATE", "pH 6.8, SMALL TUBES, temperature 292K"),
])
def test_an_invention_with_no_support_in_the_text_is_dropped(name, text):
    """The failure the guard exists for. Given text containing almost no chemistry the model
    produces a plausible reagent rather than an empty answer, and writing those into the dataset
    would make it worse than not running the model at all."""
    from toppdblx.models.apply_slm import GROUNDING_FUZZY_MIN, _best_similarity
    from toppdblx.models.eval_slm import load_aliases
    assert _best_similarity(name, text, load_aliases()) < GROUNDING_FUZZY_MIN


def test_similarity_slides_along_the_text():
    """A condition names several reagents, so comparing a name against the whole string would be
    swamped by the parts describing everything else."""
    from toppdblx.models.apply_slm import _best_similarity
    from toppdblx.models.eval_slm import load_aliases
    long_text = "0.1 M Tris pH 8.5, 20% PEG 3350, 0.2 M lithium sulfate, 3% DMSO, 5 mM TCEP"
    assert _best_similarity("PEG_3350", long_text, load_aliases()) > 0.9
