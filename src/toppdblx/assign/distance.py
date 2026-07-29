"""Condition featurisation and distance: the chemistry that decides what groups together.

Spec 6.3 is explicit that this is the technical crux, and lists five things that must be
encoded correctly or "the groups fragment for no chemical reason". Each is implemented here
with the scale that makes it true, and every scale constant carries its justification,
because these numbers are the ontology's actual content.

  1. **PEG molecular weight is ordinal, not categorical**, and log-scaled. PEG 3350 and
     PEG 4000 differ by log10(4000/3350) = 0.076, so they are near-identical. PEG 400 and
     PEG 8000 differ by 1.30, so they are different reagents. A categorical encoding would
     make those two pairs equally far apart, which is chemically false.
  2. **Buffer identity collapses to pH.** At 0.1 M a buffer mostly just sets pH, and the
     Top96 precedent is that swapping CAPS for glycine at the same pH leaves the condition
     equivalent. Buffer identity therefore contributes nothing to the distance; only the pH
     it sets does.
  3. **Concentration distance is normalised per precipitant type.** 20% against 25% PEG is a
     small step: percent is scaled at 15 points per unit, giving 0.33. 0.8 M against 2.4 M
     ammonium sulfate is a different regime: salt molarity is compared as a log ratio scaled
     by 0.5, giving 0.95. The salt step therefore outweighs the PEG step, which is the whole
     point and which the first version of these scales got backwards.
  4. **Salt identity by Hofmeister position, not string match.** Sulfate, phosphate and
     citrate sit together; thiocyanate and nitrate sit apart. The rank spans -4 to +4 and is
     scaled so that sulfate against chloride is 2 units.
  5. **pH is banded at roughly one unit**, so pH 6.4 and 6.6 are the same and pH 5 and 8 are
     not.

Missing features are not treated as zero. A condition with no PEG is not a condition with
PEG of weight one: the comparison is skipped and the weight redistributed, so absence never
masquerades as an extreme value.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Optional

# --- scales, each one a chemical claim ------------------------------------

# PEG molecular weight, log10. One unit of distance per decade, so 3350 vs 4000 is 0.076 and
# 400 vs 8000 is 1.30.
PEG_LOG_MW_SCALE = 1.0

# Percent concentration: 15 points per unit, so 20% against 25% PEG is 0.33. Deliberately
# loose, because 5 points of PEG is inside the optimisation range for the same protein.
PERCENT_SCALE = 15.0

# Salt molarity as a log ratio, halved so a threefold change is 0.95 and a tenfold change
# 2.0. Tight on purpose: spec 6.3 insists 0.8 M against 2.4 M ammonium sulfate is "a
# different regime entirely", and it must therefore outweigh a 5-point PEG step, not sit
# below it. The first version of these two scales had it the wrong way round.
SALT_LOG_RATIO_SCALE = 0.5

# Hofmeister rank spans -4 (sulfate) to +4 (thiocyanate). Dividing by 2 makes sulfate against
# chloride 2 units, which is the same order as a decade of PEG molecular weight.
HOFMEISTER_SCALE = 2.0

# pH banded at one unit.
PH_SCALE = 1.0

# Relative importance. The precipitant identity and amount dominate; pH matters but less,
# because a condition is usually reproducible across a pH unit.
WEIGHTS = {
    "peg_log_mw": 1.0,
    "peg_percent": 0.8,
    "salt_hofmeister": 0.9,
    "salt_log_molar": 0.9,
    "organic_percent": 0.7,
    "ph": 0.6,
}

_MOLAR_SCALE = {"molar": 1.0, "millimolar": 1e-3, "micromolar": 1e-6, "nanomolar": 1e-9}


@dataclass(frozen=True)
class ConditionFeatures:
    """A condition reduced to the axes that decide chemical similarity."""
    peg_log_mw: Optional[float] = None
    peg_percent: Optional[float] = None
    salt_hofmeister: Optional[float] = None
    salt_log_molar: Optional[float] = None
    organic_percent: Optional[float] = None
    ph: Optional[float] = None

    def as_dict(self) -> dict[str, Optional[float]]:
        return {
            "peg_log_mw": self.peg_log_mw, "peg_percent": self.peg_percent,
            "salt_hofmeister": self.salt_hofmeister, "salt_log_molar": self.salt_log_molar,
            "organic_percent": self.organic_percent, "ph": self.ph,
        }


def _molar(concentration: Optional[float], unit: Optional[str]) -> Optional[float]:
    if concentration is None or unit not in _MOLAR_SCALE:
        return None
    return concentration * _MOLAR_SCALE[unit]


def _percent(concentration: Optional[float], unit: Optional[str]) -> Optional[float]:
    if concentration is None or not (unit or "").startswith("percent"):
        return None
    return concentration


def featurise(components: Iterable[dict[str, Any]], ph: Optional[float] = None,
              peg_organic_max_mw: int = 600) -> ConditionFeatures:
    """Reduce a condition's components to the six comparison axes.

    Buffers are deliberately absent from the output except through the pH they set, and
    explicit cryoprotectants are excluded entirely: they are added after the drop.
    """
    peg_percent = 0.0
    peg_weighted_log_mw = 0.0
    salt_molar = 0.0
    salt_weighted_rank = 0.0
    salt_rank_weight = 0.0
    organic_percent = 0.0
    saw_peg = saw_salt = saw_organic = False

    for component in components:
        if component.get("cryo_evidence") == "explicit":
            continue
        chem_class = component.get("chem_class")
        percent = _percent(component.get("concentration"), component.get("unit"))
        molar = _molar(component.get("concentration"), component.get("unit"))

        if chem_class == "peg":
            peg_mw = component.get("peg_mw")
            # Below the boundary a PEG is an organic, so it contributes there instead.
            if peg_mw is not None and peg_mw <= peg_organic_max_mw:
                if percent:
                    organic_percent += percent
                    saw_organic = True
                continue
            if peg_mw:
                saw_peg = True
                amount = percent if percent else 1.0
                peg_percent += percent or 0.0
                peg_weighted_log_mw += math.log10(peg_mw) * amount
        elif chem_class in ("organic", "polyol"):
            if percent:
                organic_percent += percent
                saw_organic = True
        elif chem_class == "salt" or (
                chem_class == "buffer" and component.get("hofmeister_rank") is not None
                and molar is not None and molar >= 0.2):
            if molar:
                saw_salt = True
                salt_molar += molar
                rank = component.get("hofmeister_rank")
                if rank is not None:
                    salt_weighted_rank += rank * molar
                    salt_rank_weight += molar

    # The molecular weight is concentration-weighted, so in a mixed-PEG condition the
    # dominant polymer sets the value rather than an incidental trace one.
    return ConditionFeatures(
        peg_log_mw=(peg_weighted_log_mw / peg_percent if (saw_peg and peg_percent)
                    else (peg_weighted_log_mw if saw_peg else None)),
        peg_percent=peg_percent if saw_peg and peg_percent else None,
        salt_hofmeister=(salt_weighted_rank / salt_rank_weight
                         if salt_rank_weight else None),
        salt_log_molar=math.log10(salt_molar) if (saw_salt and salt_molar > 0) else None,
        organic_percent=organic_percent if saw_organic and organic_percent else None,
        ph=ph,
    )


PRECIPITANT_AXES = ("peg_log_mw", "peg_percent", "salt_hofmeister", "salt_log_molar",
                    "organic_percent")


def shared_axes(a: ConditionFeatures, b: ConditionFeatures) -> list[str]:
    """Axes both conditions actually have. A small distance over one axis is not similarity."""
    left, right = a.as_dict(), b.as_dict()
    return [axis for axis in WEIGHTS if left[axis] is not None and right[axis] is not None]


def shares_precipitant_axis(a: ConditionFeatures, b: ConditionFeatures) -> bool:
    """True when the two agree on something that actually precipitates protein.

    Two conditions matching on pH alone are not chemically similar, and treating them as
    such lets a condition with no identified precipitant anchor itself to a real screen well.
    """
    return any(axis in PRECIPITANT_AXES for axis in shared_axes(a, b))


def distance(a: ConditionFeatures, b: ConditionFeatures) -> float:
    """Weighted distance over the axes both conditions share.

    Axes present in only one condition are skipped rather than imputed. Imputing zero would
    say a PEG-free condition contains PEG at 10^0 = 1 Da, which is worse than saying nothing.
    The remaining weight is renormalised so distances stay comparable across pairs that share
    different numbers of axes.
    """
    left, right = a.as_dict(), b.as_dict()
    total = 0.0
    used_weight = 0.0

    for axis, weight in WEIGHTS.items():
        x, y = left[axis], right[axis]
        if x is None or y is None:
            continue
        if axis == "peg_log_mw":
            step = abs(x - y) / PEG_LOG_MW_SCALE
        elif axis in ("peg_percent", "organic_percent"):
            step = abs(x - y) / PERCENT_SCALE
        elif axis == "salt_log_molar":
            step = abs(x - y) / SALT_LOG_RATIO_SCALE
        elif axis == "salt_hofmeister":
            step = abs(x - y) / HOFMEISTER_SCALE
        else:
            step = abs(x - y) / PH_SCALE
        total += weight * step * step
        used_weight += weight

    if used_weight == 0:
        # Nothing comparable. Maximal distance rather than zero, which would otherwise make
        # two conditions with no shared axes look identical.
        return float("inf")
    return math.sqrt(total / used_weight)
