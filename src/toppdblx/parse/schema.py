"""The parsed condition record schema.

Spec 5.5: "Fix this schema before writing anything downstream. Every later stage inherits
it." This is that schema, as pydantic models, with the deviations from the spec draft
recorded here rather than discovered later:

  * The key is `(pdb_id, crystal_id)`, not `pdb_id`. 390 entries describe several crystal
    forms, one of them 21, and they are genuinely different conditions.
  * Components carry `cryo_evidence` (`explicit` or `inferred`). Only 2.2% of entries name
    a cryoprotectant, so an inferred cryo label is a guess and is marked as one.
  * Components carry `premix_id` and premixes expand to constituents, which fixes the
    representation half of decision 12.2 without prejudging the Phase 1 taxonomy.
  * `curated_group` is present but always null in Phase 0, so Phase 1 is a join rather than
    a schema migration.
  * `unit_inferred` is mandatory, not decorative: about 80% of percentage concentrations in
    this corpus carry no w/v or v/v marker, so the defaulting rule is doing most of the
    work and downstream consumers must be able to see where it fired.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .. import config

# `not_a_component` is distinct from `unknown`. `unknown` means "a reagent the lexicon does not
# recognise", and belongs in the identification denominator as a genuine miss. `not_a_component`
# means "there is no reagent in this text at all" (method notes, screen references, an unnamed
# inhibitor), and must be excluded from that denominator: no lexicon entry could ever match it,
# so scoring it as a failure to identify measures an artefact rather than the parser. Measured at
# 10,206 components, worth 1.36 points of apparent identification.
# `protein_buffer` and `soak` are scope, not chemistry, and the distinction is the one the role
# vocabulary was missing. A reagent in a protein storage buffer or a post-growth soak *is* really
# there and *is* correctly named, so `not_a_component` is a lie about it and every chemistry role
# is a lie about what it did: the crystal did not grow in it. Without somewhere to put them the
# only options were to emit them as components, which is a false positive, or to drop them, which
# throws away a correct reading and teaches the model that text it read properly was wrong.
#
# Measured on the 192 gold records: 26 of the 32B teacher's 47 false positives are exactly this,
# 55% of everything it gets wrong. Fourteen are protein storage buffers ("Protein solution was at
# 20 mg/mL containing 50 mM Tris, 100 mM NaCl, 10 mM EDTA"), five are soaks, and the rest are
# cryo steps that `cryo` already covers.
Role = Literal["precipitant", "salt", "buffer", "additive", "cryo", "protein",
               "protein_buffer", "soak", "not_a_component", "unknown"]

# Roles that name a real, correctly-read reagent that the crystal nonetheless did not grow in.
# Everything downstream that asks "what was in the condition" filters on this rather than
# re-deriving the list, so adding a scope role in one place propagates.
OUT_OF_SCOPE_ROLES = frozenset({"protein_buffer", "soak"})

# Why a clause was judged to contain no reagent. Kept alongside the role so the decision is
# auditable: a false positive here silently deletes real chemistry, so it must never be a
# verdict without a recorded reason.
NonComponentReason = Literal["method_text", "unnamed_macromolecule", "screen_reference",
                             "publication_reference", "splitter_fragment", "buffer_titrant"]

Unit = Literal["percent_w_v", "percent_v_v", "percent_unspecified",
               "molar", "millimolar", "micromolar", "nanomolar",
               "mg_ml", "g_l", "unitless"]

PhSource = Literal["buffer", "final", "unstated"]

TemperatureSource = Literal["reported", "text", "both_agree", "conflict"]

CryoEvidence = Literal["explicit", "inferred"]

Parser = Literal["rules_v1", "rules_v2", "rules_v3", "slm_v1", "slm_v2", "manual"]

# Stable discard codes. Every rejected record carries exactly one, and the distribution is
# a published result in its own right (spec 5.1).
DiscardReason = Literal[
    "EMPTY",                    # no details string at all
    "TOO_SHORT",                # too little text to contain a condition
    "REFERENCE_ONLY",           # defers to the literature
    "METHOD_ONLY",              # method, temperature or pH but no reagents
    "NO_REAGENT_MATCH",         # reagent clauses present, none identified
    "UNPARSEABLE_RESIDUAL",     # most of the text left uncovered
    "NON_CRYSTALLISATION_TEXT",
]


class Component(BaseModel, extra="forbid"):
    role: Role = "unknown"
    name_raw: str
    name_canonical: Optional[str] = None
    chem_class: Optional[str] = None

    peg_mw: Optional[int] = None
    is_mme: bool = False
    hofmeister_rank: Optional[int] = None
    buffer_pka: Optional[float] = None

    concentration: Optional[float] = None
    unit: Optional[Unit] = None
    # True when the unit came from the reagent's default rather than the text. The single
    # most consequential inference the parser makes.
    unit_inferred: bool = False
    concentration_is_range: bool = False
    concentration_range: Optional[tuple[Optional[float], Optional[float]]] = None

    cryo_evidence: Optional[CryoEvidence] = None
    premix_id: Optional[str] = None
    parse_confidence: float = 1.0
    # Set only when role is `not_a_component`, so the exclusion can be audited by reason.
    non_component_reason: Optional[NonComponentReason] = None


class Provenance(BaseModel, extra="forbid"):
    parser: Parser
    parse_confidence: float
    flags: list[str] = Field(default_factory=list)
    n_clauses: int = 0
    n_clauses_identified: int = 0
    unidentified_clauses: list[str] = Field(default_factory=list)


class ConditionRecord(BaseModel, extra="forbid"):
    pdb_id: str
    crystal_id: str
    entry_version: Optional[str] = None
    raw_details: Optional[str] = None

    method: Optional[str] = None
    diffraction_method: Optional[str] = None

    temperature_k: Optional[float] = None
    temperature_source: Optional[TemperatureSource] = None

    ph: Optional[float] = None
    ph_source: PhSource = "unstated"
    ph_is_range: bool = False
    ph_range: Optional[tuple[Optional[float], Optional[float]]] = None
    ph_reported: Optional[float] = None

    components: list[Component] = Field(default_factory=list)

    # Usually absent (spec 11.4) but captured when present, because nobody else does.
    protein_concentration_mg_ml: Optional[float] = None
    drop_ratio: Optional[str] = None

    # Phase 1 fills these. Present now so the later stages are a join, not a migration.
    curated_group: Optional[dict] = None
    commercial_screen_match: Optional[dict] = None

    provenance: Provenance
    discard_reason: Optional[DiscardReason] = None

    schema_version: str = config.SCHEMA_VERSION
    ontology_version: str = config.ONTOLOGY_VERSION

    @property
    def is_usable(self) -> bool:
        return self.discard_reason is None and bool(self.components)
