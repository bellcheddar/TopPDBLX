"""The reagent lexicon: schema, loader and alias index.

`ontology/synonyms.yaml` is the load-bearing curation artefact of Phase 0. Every downstream
stage keys off the canonical ids defined there, so the file is validated strictly on load
and an alias collision is a hard error rather than a warning: two reagents claiming the
same alias would silently resolve to whichever happened to load first.

Three fields encode the chemistry the spec calls out in section 6.3, and they exist here
rather than in the assignment code so they can be curated as data:

  peg_mw            PEG molecular weight, ordinal and log-scaled downstream. PEG 3350 and
                    PEG 4000 are near-interchangeable; PEG 400 and PEG 8000 are not the
                    same kind of reagent at all.
  hofmeister_rank   salts are grouped by Hofmeister position, not string match. Negative is
                    kosmotropic (sulfate, phosphate, citrate), positive is chaotropic
                    (nitrate, iodide, thiocyanate), 0 is chloride.
  buffer_pka        buffers collapse to the pH they set, so identity matters far less than
                    pKa. Carrying the pKa makes that collapse checkable.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .. import config
from .text import normalise, tidy_name

LEXICON_PATH = config.ONTOLOGY_DIR / "synonyms.yaml"

ChemClass = Literal["peg", "organic", "salt", "buffer", "polyol", "detergent",
                    "premix", "additive", "other"]

Unit = Literal["percent_w_v", "percent_v_v", "molar", "millimolar", "mg_ml", "unitless"]


class Reagent(BaseModel, extra="forbid"):
    canonical_id: str
    display_name: str
    chem_class: ChemClass
    aliases: list[str] = Field(default_factory=list)
    default_unit: Optional[Unit] = None
    peg_mw: Optional[int] = None
    is_mme: bool = False
    hofmeister_rank: Optional[int] = None
    buffer_pka: Optional[float] = None
    # Premixed stocks (Tacsimate, Morpheus mixes, MIB) expand to constituents where the
    # vendor publishes them. Spec decision 12.2: the representation is fixed in Phase 0,
    # the taxonomy is a Phase 1 question.
    premix_components: list[str] = Field(default_factory=list)
    note: Optional[str] = None

    @field_validator("canonical_id")
    @classmethod
    def _upper_snake(cls, value: str) -> str:
        if not value.replace("_", "").replace("-", "").isalnum() or value != value.upper():
            raise ValueError(f"canonical_id must be upper snake case: {value!r}")
        return value

    @model_validator(mode="after")
    def _class_consistency(self) -> "Reagent":
        if self.chem_class == "peg" and self.peg_mw is None:
            raise ValueError(f"{self.canonical_id}: chem_class 'peg' requires peg_mw")
        if self.peg_mw is not None and self.chem_class != "peg":
            raise ValueError(f"{self.canonical_id}: peg_mw set on non-peg class")
        if self.chem_class == "premix" and not self.premix_components:
            raise ValueError(f"{self.canonical_id}: premix requires premix_components")
        return self

    @property
    def all_names(self) -> list[str]:
        return [self.display_name, *self.aliases]


class Lexicon(BaseModel, extra="forbid"):
    version: str
    reagents: list[Reagent]

    @model_validator(mode="after")
    def _unique_ids_and_aliases(self) -> "Lexicon":
        ids = [r.canonical_id for r in self.reagents]
        duplicated = {i for i in ids if ids.count(i) > 1}
        if duplicated:
            raise ValueError(f"duplicate canonical_id: {sorted(duplicated)}")

        owners: defaultdict[str, list[str]] = defaultdict(list)
        for reagent in self.reagents:
            for name in reagent.all_names:
                owners[normalise(tidy_name(name))].append(reagent.canonical_id)
        clashes = {alias: sorted(set(who)) for alias, who in owners.items() if len(set(who)) > 1}
        if clashes:
            raise ValueError(f"alias claimed by more than one reagent: {clashes}")

        # A premix pointing at a canonical_id that does not exist would silently drop its
        # constituents at expansion time.
        known = set(ids)
        for reagent in self.reagents:
            unknown = [c for c in reagent.premix_components if c not in known]
            if unknown:
                raise ValueError(
                    f"{reagent.canonical_id}: premix_components reference unknown ids {unknown}"
                )
        return self

    def index(self) -> dict[str, Reagent]:
        """Normalised alias -> Reagent, for exact lookup by the parser.

        Keys go through the same tidying the parser applies to candidate names, so both
        sides of the lookup agree on hydration state, oxidation state and trademarks.
        """
        return {normalise(tidy_name(name)): reagent
                for reagent in self.reagents
                for name in reagent.all_names}

    def by_id(self) -> dict[str, Reagent]:
        return {r.canonical_id: r for r in self.reagents}


def load(path: Optional[Path] = None) -> Lexicon:
    path = path or LEXICON_PATH
    if not path.exists():
        raise FileNotFoundError(f"lexicon not found at {path}")
    return Lexicon.model_validate(yaml.safe_load(path.read_text()))
