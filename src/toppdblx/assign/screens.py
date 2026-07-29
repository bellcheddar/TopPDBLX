"""Screen library loading and condition fingerprints.

Each well is parsed with the **same parser used on the PDB text**, so matching is schema to
schema rather than string to string. That has a useful side effect: the vendor strings are
clean, well-formed prose, so the fraction of wells the parser fully resolves is a direct
check on the parser against text that ought to be easy. A parser that stumbles on
`0.2 M Magnesium chloride hexahydrate, 0.1 M TRIS hydrochloride pH 8.5, 30% w/v
Polyethylene glycol 4,000` has no business on the messy corpus.

It also introduces a limit worth stating plainly: a systematic lexicon error affects both
sides identically and is therefore invisible to this check. Screen matching validates
concentration and structure reading, not reagent naming. Naming is what the WP8 hand audit
is for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import yaml

from .. import config
from ..parse.rules import RuleParser
from ..parse.schema import Component

SCREENS_DIR = config.ONTOLOGY_DIR / "screens"

# Per-class concentration tolerance for calling a match exact. Spec 6.3: distance must be
# normalised per precipitant type, because 20% against 25% PEG is a small step while 0.8 M
# against 2.4 M ammonium sulfate is a different regime entirely.
ABSOLUTE_PERCENT_TOLERANCE = 2.0     # percentage points
RELATIVE_TOLERANCE = 0.15            # molar, millimolar and the rest
PH_TOLERANCE = 0.3


@dataclass(frozen=True)
class Well:
    screen: str
    catalogue: str
    well: str
    condition_text: str
    components: tuple[Component, ...]
    ph: Optional[float]
    fingerprint: frozenset[str]
    resolved: bool


@dataclass
class ScreenLibrary:
    wells: list[Well] = field(default_factory=list)

    def by_fingerprint(self) -> dict[frozenset[str], list[Well]]:
        index: dict[frozenset[str], list[Well]] = {}
        for well in self.wells:
            if well.resolved:
                index.setdefault(well.fingerprint, []).append(well)
        return index

    @property
    def n_resolved(self) -> int:
        return sum(1 for w in self.wells if w.resolved)


def fingerprint(components: Iterable[Component]) -> frozenset[str]:
    """The component set used for matching.

    Explicitly declared cryoprotectants are excluded: they are added after the drop is set
    and are not part of the crystallisation condition. Components whose cryo role was only
    *inferred* stay in, because a third of glycerol values in this corpus sit above 15%,
    which is precipitant scale, and dropping those would corrupt otherwise correct matches.
    Unresolved components are kept as a sentinel so a record containing them cannot match
    a well exactly.
    """
    names = set()
    for component in components:
        if component.cryo_evidence == "explicit":
            continue
        names.add(component.name_canonical or f"?{component.name_raw[:24]}")
    return frozenset(names)


# Molar-family units all reduce to molar before comparison. A screen well printed as
# "0.1 M" and a deposition written as "100 mM" are the same concentration; comparing the
# unit strings instead of the quantities scored 11.6% of matched components as
# disagreements purely because the depositor chose different units from the vendor.
_MOLAR_SCALE = {"molar": 1.0, "millimolar": 1e-3, "micromolar": 1e-6, "nanomolar": 1e-9}
_MASS_SCALE = {"mg_ml": 1.0, "g_l": 1.0}          # 1 mg/ml == 1 g/l


def to_comparable(component: Component) -> tuple[Optional[str], Optional[float]]:
    """Reduce a concentration to (family, canonical value), or (None, None).

    Percent w/v and percent v/v stay in separate families: they measure different things
    and a 30% v/v organic is not a 30% w/v polymer.
    """
    unit, value = component.unit, component.concentration
    if unit is None or value is None:
        return None, None
    if unit in _MOLAR_SCALE:
        return "molar", value * _MOLAR_SCALE[unit]
    if unit in _MASS_SCALE:
        return "mass_per_volume", value * _MASS_SCALE[unit]
    if unit.startswith("percent"):
        return unit, value
    return unit, value


def concentrations_agree(a: Component, b: Component) -> bool:
    if a.concentration is None or b.concentration is None:
        return a.concentration == b.concentration
    family_a, value_a = to_comparable(a)
    family_b, value_b = to_comparable(b)
    if family_a is None or family_a != family_b:
        return False
    if family_a.startswith("percent"):
        return abs(value_a - value_b) <= ABSOLUTE_PERCENT_TOLERANCE
    larger = max(abs(value_a), abs(value_b), 1e-12)
    return abs(value_a - value_b) / larger <= RELATIVE_TOLERANCE


def load(parser: RuleParser, screens_dir: Path = SCREENS_DIR) -> ScreenLibrary:
    library = ScreenLibrary()
    for path in sorted(screens_dir.glob("*.yaml")):
        document = yaml.safe_load(path.read_text())
        for entry in document.get("wells", []):
            record = parser.parse(document["catalogue"], entry["well"],
                                  entry["condition_text"])
            components = tuple(record.components)
            library.wells.append(Well(
                screen=document["screen"],
                catalogue=document["catalogue"],
                well=str(entry["well"]),
                condition_text=entry["condition_text"],
                components=components,
                ph=record.ph,
                fingerprint=fingerprint(components),
                resolved=bool(components) and all(c.name_canonical for c in components),
            ))
    return library
