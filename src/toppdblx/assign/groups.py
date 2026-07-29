"""The curated condition group ontology: schema, loader and validation.

`ontology/groups.yaml` is to Phase 1 what `synonyms.yaml` was to Phase 0: the artefact a
human owns and everything downstream keys off. It is validated strictly on load, and a
duplicate id or a group with no centroid is a hard error rather than a warning.

Spec 6.1 is emphatic that groups are **hand-defined and human-readable**, with clustering
used only as a diagnostic, because "emergent clustering alone produces chemically meaningless
groups and unorderable output". So each group carries three things:

  label            a sentence a crystallographer can read and disagree with
  centroid         the position in feature space that `assign.distance` measures against
  screen_anchors   real orderable wells that sit in this group, so the output can say
                   "maps to Crystal Screen 32, already in the fridge" (spec 6.5)

`assign.build_groups` proposes the file from the corpus so the starting point is derived from
the data rather than invented, and it is then Marc's to edit. The version is semantic and the
changelog is required, because every model trained afterwards is tied to a specific ontology
version (spec 6.6).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .. import config
from .distance import ConditionFeatures

GROUPS_PATH = config.ONTOLOGY_DIR / "groups.yaml"

L1_CLASSES = {"Organic", "Organic/PEG", "Organic/PEG/Salt", "Organic/Salt",
              "PEG", "Salt", "Salt/PEG", "Unassigned"}


class Centroid(BaseModel, extra="forbid"):
    """A group's position on the six comparison axes. Absent axes mean "not applicable"."""
    peg_log_mw: Optional[float] = None
    peg_percent: Optional[float] = None
    salt_hofmeister: Optional[float] = None
    salt_log_molar: Optional[float] = None
    organic_percent: Optional[float] = None
    ph: Optional[float] = None

    def to_features(self) -> ConditionFeatures:
        return ConditionFeatures(**self.model_dump())

    @model_validator(mode="after")
    def _not_entirely_empty(self) -> "Centroid":
        if all(v is None for v in self.model_dump().values()):
            raise ValueError("centroid has no axes: nothing could ever match it")
        return self


class ScreenAnchor(BaseModel, extra="forbid"):
    screen: str
    catalogue: str
    well: str


class Group(BaseModel, extra="forbid"):
    id: str
    level: int
    label: str
    l1_class: str
    centroid: Centroid
    parent: Optional[str] = None
    n_records_at_creation: int = 0
    screen_anchors: list[ScreenAnchor] = Field(default_factory=list)
    note: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _upper_snake(cls, value: str) -> str:
        if value != value.upper() or " " in value:
            raise ValueError(f"group id must be upper snake case: {value!r}")
        return value

    @field_validator("level")
    @classmethod
    def _level_range(cls, value: int) -> int:
        if value not in (2, 3):
            raise ValueError("level must be 2 or 3; L1 is derived, not curated")
        return value

    @field_validator("l1_class")
    @classmethod
    def _known_class(cls, value: str) -> str:
        if value not in L1_CLASSES:
            raise ValueError(f"unknown L1 class {value!r}; expected one of {sorted(L1_CLASSES)}")
        return value


class Ontology(BaseModel, extra="forbid"):
    version: str
    groups: list[Group]

    @model_validator(mode="after")
    def _consistent(self) -> "Ontology":
        ids = [g.id for g in self.groups]
        duplicated = {i for i, n in Counter(ids).items() if n > 1}
        if duplicated:
            raise ValueError(f"duplicate group id: {sorted(duplicated)}")

        known = set(ids)
        for group in self.groups:
            # An L3 group whose parent does not exist could never fall back to L2, which is
            # the whole mechanism for handling the long tail (spec 6.2).
            if group.parent and group.parent not in known:
                raise ValueError(f"{group.id}: parent {group.parent!r} does not exist")
            if group.level == 3 and group.parent is None:
                raise ValueError(f"{group.id}: an L3 group must name its L2 parent")
            if group.parent:
                parent = next(g for g in self.groups if g.id == group.parent)
                if parent.level != 2:
                    raise ValueError(f"{group.id}: parent {parent.id} is not an L2 group")
                if parent.l1_class != group.l1_class:
                    raise ValueError(
                        f"{group.id}: L1 class {group.l1_class!r} disagrees with parent "
                        f"{parent.id} ({parent.l1_class!r})")
        return self

    def by_level(self, level: int) -> list[Group]:
        return [g for g in self.groups if g.level == level]

    def by_id(self) -> dict[str, Group]:
        return {g.id: g for g in self.groups}


def load(path: Optional[Path] = None) -> Ontology:
    path = path or GROUPS_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"no group ontology at {path}. Propose one with: ./run.sh assign.build_groups")
    return Ontology.model_validate(yaml.safe_load(path.read_text()))
