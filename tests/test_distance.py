"""Tests for condition featurisation and distance.

Spec 6.3 lists five things that must be encoded correctly "or the groups fragment for no
chemical reason". Each is asserted here as a chemical claim, not just as code behaviour,
because these scales are the ontology's actual content and a silent change to one would
reshape every group.
"""

from __future__ import annotations

import math

import pytest

from toppdblx.assign.distance import ConditionFeatures, distance, featurise


def component(**kwargs):
    base = {"chem_class": None, "concentration": None, "unit": None, "peg_mw": None,
            "cryo_evidence": None, "hofmeister_rank": None, "premix_id": None}
    base.update(kwargs)
    return base


def peg(mw, percent):
    return component(chem_class="peg", peg_mw=mw, concentration=percent, unit="percent_w_v")


def salt(molar, rank=0):
    return component(chem_class="salt", concentration=molar, unit="molar",
                     hofmeister_rank=rank)


# --- 1. PEG molecular weight is ordinal and log-scaled --------------------

def test_peg_3350_and_4000_are_near_interchangeable():
    """Spec 6.3 states this explicitly. log10(4000/3350) = 0.076."""
    a = featurise([peg(3350, 20)])
    b = featurise([peg(4000, 20)])
    assert distance(a, b) < 0.1


def test_peg_400_and_peg_8000_are_far_apart():
    """One behaves as an organic, the other as a true polymer precipitant."""
    a = featurise([peg(1000, 20)])
    b = featurise([peg(8000, 20)])
    assert distance(a, b) > distance(featurise([peg(3350, 20)]), featurise([peg(4000, 20)]))


def test_peg_distance_is_logarithmic_not_linear():
    """A categorical or linear encoding would make these two gaps comparable; they are not."""
    close = distance(featurise([peg(3350, 20)]), featurise([peg(4000, 20)]))
    far = distance(featurise([peg(1000, 20)]), featurise([peg(10000, 20)]))
    assert far > 10 * close


def test_a_low_molecular_weight_peg_is_featurised_as_an_organic():
    features = featurise([peg(400, 25)])
    assert features.peg_log_mw is None
    assert features.organic_percent == 25


# --- 2. buffer identity collapses to pH ----------------------------------

def test_swapping_the_buffer_at_the_same_ph_changes_nothing():
    """Top96 precedent: CAPS for glycine at the same pH is the same condition."""
    a = featurise([peg(3350, 20), component(chem_class="buffer", concentration=0.1,
                                            unit="molar")], ph=7.5)
    b = featurise([peg(3350, 20), component(chem_class="buffer", concentration=0.1,
                                            unit="molar")], ph=7.5)
    assert distance(a, b) == 0.0


def test_buffer_identity_never_reaches_the_feature_vector():
    features = featurise([component(chem_class="buffer", concentration=0.1, unit="molar")],
                         ph=7.0)
    assert features.as_dict() == {"peg_log_mw": None, "peg_percent": None,
                                  "salt_hofmeister": None, "salt_log_molar": None,
                                  "organic_percent": None, "ph": 7.0}


# --- 3. concentration normalised per precipitant type --------------------

def test_twenty_versus_twentyfive_percent_peg_is_a_small_step():
    a, b = featurise([peg(3350, 20)]), featurise([peg(3350, 25)])
    assert 0 < distance(a, b) < 0.7


def test_point_eight_versus_two_point_four_molar_salt_is_a_different_regime():
    """Spec 6.3 contrasts these two cases directly, and they must not be equivalent."""
    peg_step = distance(featurise([peg(3350, 20)]), featurise([peg(3350, 25)]))
    salt_step = distance(featurise([salt(0.8)]), featurise([salt(2.4)]))
    assert salt_step > peg_step


def test_salt_molarity_is_compared_as_a_ratio_not_a_difference():
    """0.1 to 0.3 M is a threefold change and should equal 1.0 to 3.0 M."""
    low = distance(featurise([salt(0.1)]), featurise([salt(0.3)]))
    high = distance(featurise([salt(1.0)]), featurise([salt(3.0)]))
    assert low == pytest.approx(high, rel=1e-6)


# --- 4. salt identity by Hofmeister position -----------------------------

def test_kosmotropes_sit_together_and_chaotropes_apart():
    sulfate = featurise([salt(1.0, rank=-4)])
    phosphate = featurise([salt(1.0, rank=-3)])
    thiocyanate = featurise([salt(1.0, rank=4)])
    assert distance(sulfate, phosphate) < distance(sulfate, thiocyanate)


def test_hofmeister_rank_is_concentration_weighted():
    """A trace salt must not drag the family of a dominant one."""
    features = featurise([salt(2.0, rank=-4), salt(0.01, rank=4)])
    assert features.salt_hofmeister < -3.9


# --- 5. pH banded at about one unit --------------------------------------

def test_ph_is_banded_so_a_fraction_of_a_unit_barely_counts():
    """Stated as a relationship rather than an absolute, which is what the spec claims and
    what survives a change of scale: 0.2 of a pH unit must be far smaller than 3 units."""
    within_band = distance(featurise([peg(3350, 20)], ph=6.4),
                           featurise([peg(3350, 20)], ph=6.6))
    across_bands = distance(featurise([peg(3350, 20)], ph=5.0),
                            featurise([peg(3350, 20)], ph=8.0))
    assert within_band * 10 < across_bands


# --- missing axes are skipped, never imputed -----------------------------

def test_absent_peg_is_not_treated_as_peg_of_weight_one():
    """Imputing zero would claim a PEG-free condition contains PEG at 10^0 = 1 Da."""
    with_peg = featurise([peg(3350, 20), salt(1.0)])
    without = featurise([salt(1.0)])
    # Comparable only on the shared salt axis, so identical salts give distance zero.
    assert distance(with_peg, without) == 0.0


def test_conditions_with_nothing_in_common_are_infinitely_far():
    """Zero would say two conditions sharing no axis are identical."""
    assert math.isinf(distance(featurise([peg(3350, 20)]), featurise([salt(1.0)])))


def test_distance_is_symmetric_and_zero_on_itself():
    a = featurise([peg(3350, 20), salt(0.2, rank=-4)], ph=7.0)
    b = featurise([peg(4000, 22), salt(0.3, rank=-3)], ph=6.5)
    assert distance(a, a) == 0.0
    assert distance(a, b) == pytest.approx(distance(b, a))


# --- exclusions ----------------------------------------------------------

def test_explicit_cryoprotectant_is_excluded_from_the_features():
    with_cryo = featurise([peg(3350, 20),
                           component(chem_class="polyol", concentration=15,
                                     unit="percent_v_v", cryo_evidence="explicit")])
    assert with_cryo.organic_percent is None


def test_inferred_cryo_is_kept():
    features = featurise([component(chem_class="polyol", concentration=10,
                                    unit="percent_v_v", cryo_evidence="inferred")])
    assert features.organic_percent == 10


def test_high_concentration_dual_role_buffer_counts_as_salt():
    features = featurise([component(chem_class="buffer", concentration=0.8, unit="molar",
                                    hofmeister_rank=-3)])
    assert features.salt_log_molar is not None
    assert features.salt_hofmeister == pytest.approx(-3)


def test_empty_condition_gives_all_none():
    assert featurise([]) == ConditionFeatures()
