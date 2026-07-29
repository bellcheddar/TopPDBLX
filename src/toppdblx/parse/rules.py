"""The deterministic rule parser: free text to schema-valid components.

Spec 5.3: write the deterministic parser first, and expect it to plateau around 75%. Its
output bootstraps the labels for the small language model that handles the residual, and it
remains the parser of record for everything it is confident about.

The three judgement calls it makes, all of them recorded in the output rather than hidden:

  unit inference    ~80% of percentage concentrations carry no w/v or v/v marker. Resolved
                    from the reagent's chemistry and flagged with `unit_inferred`.
  pH attribution    a pH attached to a buffer clause is the buffer's; a standalone "pH x"
                    is the final pH only when the text says so. Anything else is `unstated`
                    rather than quietly assumed.
  cryo attribution  only ~2.2% of entries name a cryoprotectant. Reagents that are merely
                    capable of the role, at cryo-scale concentrations, are labelled
                    `inferred` and never confused with the explicit cases.
"""

from __future__ import annotations

from typing import Optional

import regex as re

from . import quantity
from .lexicon import Lexicon, Reagent
from .schema import Component, ConditionRecord, DiscardReason, Provenance
from .text import classify, clauses, is_noise, normalise, split_trailing_ph, strip_quantity

PARSER_VERSION = "rules_v3"

# Reagents that can act either as a cryoprotectant or as a genuine component of the drop.
# Membership alone is never enough to call something cryo: see `_cryo_evidence`.
CRYO_CAPABLE = {
    "GLYCEROL", "ETHYLENE_GLYCOL", "MPD", "PEG_200", "PEG_300", "PEG_400", "PEG_600",
    "SUCROSE", "TREHALOSE", "XYLITOL", "SORBITOL", "PROPANEDIOL_12", "BUTANEDIOL_23",
}

# Above this, a cryo-capable polyol or organic is doing precipitant work, not cryo work.
# A third of glycerol values in this corpus sit above 15%.
CRYO_MAX_PERCENT = 15.0

_EXPLICIT_CRYO = re.compile(r"cryo(?:protect\w*|preserv\w*)?|for\s+freezing|flash[- ]?(?:cool|froz)")
_FINAL_PH = re.compile(r"final\s+ph|ph\s+of\s+the\s+(?:drop|mixture|solution)")
# A bare number in the pH range at the end of a buffer name. Bounded to 0-14 so a molecular
# weight or a concentration cannot be mistaken for a pH.
_TRAILING_BARE_PH = re.compile(r"\s+((?:1[0-4]|\d)(?:\.\d+)?)\s*$")
_PH_VALUE = re.compile(r"ph\s*[:=]?\s*(\d+(?:\.\d+)?)(?:\s*(?:-|to)\s*(\d+(?:\.\d+)?))?")
_TEMP_K = re.compile(r"(\d+(?:\.\d+)?)\s*(?:k\b|kelvin)")
_TEMP_C = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:deg(?:rees)?\s*)?c(?:elsius)?\b")
_TEMP_BARE = re.compile(r"temperature\s*[:=]?\s*(\d+(?:\.\d+)?)")
_ROOM_TEMP = re.compile(r"room\s+temperature|\brt\b|ambient")
_PROTEIN_CONC = re.compile(r"(\d+(?:\.\d+)?)\s*mg\s*/\s*ml")
_DROP_RATIO = re.compile(r"\b(\d)\s*:\s*(\d)\b")

ROOM_TEMPERATURE_K = 293.0
MIN_USEFUL_LENGTH = 8


def _role_for(reagent: Optional[Reagent], lexicon: Lexicon) -> str:
    if reagent is None:
        return "unknown"
    mapping = {
        "peg": "precipitant",
        "salt": "salt",
        "buffer": "buffer",
        "organic": "precipitant",
        "polyol": "precipitant",
        "detergent": "additive",
        "additive": "additive",
        "other": "precipitant",
    }
    if reagent.chem_class == "premix":
        # A premix of buffers (MIB, MMT, MES/imidazole) is a buffer system; a premix of
        # acid salts (Tacsimate) is a precipitant.
        by_id = lexicon.by_id()
        classes = {by_id[c].chem_class for c in reagent.premix_components if c in by_id}
        return "buffer" if classes and classes <= {"buffer"} else "precipitant"
    return mapping.get(reagent.chem_class, "unknown")


def _cryo_evidence(reagent: Optional[Reagent], qty: quantity.Quantity,
                   unit: Optional[str], explicit_in_text: bool) -> Optional[str]:
    """Explicit only when the text says so. Otherwise inferred, or nothing at all."""
    if explicit_in_text:
        return "explicit"
    if reagent is None or reagent.canonical_id not in CRYO_CAPABLE:
        return None
    if unit in ("percent_v_v", "percent_w_v", "percent_unspecified") and qty.value is not None:
        return "inferred" if qty.value <= CRYO_MAX_PERCENT else None
    return None


class RuleParser:
    def __init__(self, lexicon: Lexicon):
        self.lexicon = lexicon
        self.index = lexicon.index()

    # -- components --------------------------------------------------------

    def parse_component(self, clause: str, explicit_cryo: bool) -> tuple[Component, Optional[str]]:
        """Parse one reagent clause. Returns the component and any buffer pH found on it."""
        body, attached_ph = split_trailing_ph(clause)
        qty = quantity.extract(body)
        name = strip_quantity(body)
        reagent = self.index.get(normalise(name)) if name else None

        # "hepes 7.5", "mes 6.5": a buffer named with a bare pH and no "pH" token. Retrying
        # without the trailing number resolves it, and the number is kept as the buffer pH.
        # Doing this in the lexicon would need one alias per buffer per pH value.
        if reagent is None and name:
            trimmed = _TRAILING_BARE_PH.sub("", name).strip()
            if trimmed and trimmed != name:
                candidate = self.index.get(normalise(trimmed))
                if candidate is not None and candidate.chem_class == "buffer":
                    reagent, name = candidate, trimmed
                    if attached_ph is None:
                        attached_ph = _TRAILING_BARE_PH.search(
                            strip_quantity(body)).group(1)

        unit, inferred = quantity.infer_unit(
            qty.unit,
            reagent.chem_class if reagent else None,
            reagent.peg_mw if reagent else None,
            reagent.default_unit if reagent else None,
        )

        role = _role_for(reagent, self.lexicon)
        evidence = _cryo_evidence(reagent, qty, unit, explicit_cryo)
        if evidence is not None:
            role = "cryo"

        component = Component(
            role=role,
            name_raw=name or clause,
            name_canonical=reagent.canonical_id if reagent else None,
            chem_class=reagent.chem_class if reagent else None,
            peg_mw=reagent.peg_mw if reagent else None,
            is_mme=reagent.is_mme if reagent else False,
            hofmeister_rank=reagent.hofmeister_rank if reagent else None,
            buffer_pka=reagent.buffer_pka if reagent else None,
            concentration=qty.value,
            unit=unit,
            unit_inferred=inferred,
            concentration_is_range=qty.is_range,
            concentration_range=(qty.low, qty.high) if qty.is_range else None,
            cryo_evidence=evidence,
            premix_id=reagent.canonical_id if reagent and reagent.chem_class == "premix" else None,
            parse_confidence=1.0 if reagent else 0.2,
        )
        buffer_ph = attached_ph if (reagent and reagent.chem_class == "buffer") else None
        return component, buffer_ph

    # -- record ------------------------------------------------------------

    def parse(
        self,
        pdb_id: str,
        crystal_id: str,
        raw_details: Optional[str],
        *,
        method: Optional[str] = None,
        diffraction_method: Optional[str] = None,
        temp_k_reported: Optional[float] = None,
        ph_reported: Optional[float] = None,
        entry_version: Optional[str] = None,
    ) -> ConditionRecord:
        flags: list[str] = []

        def empty_record(reason: DiscardReason, confidence: float = 0.0) -> ConditionRecord:
            return ConditionRecord(
                pdb_id=pdb_id, crystal_id=crystal_id, entry_version=entry_version,
                raw_details=raw_details, method=method,
                diffraction_method=diffraction_method,
                temperature_k=temp_k_reported,
                temperature_source="reported" if temp_k_reported is not None else None,
                ph=ph_reported, ph_reported=ph_reported,
                provenance=Provenance(parser=PARSER_VERSION, parse_confidence=confidence,
                                      flags=flags),
                discard_reason=reason,
            )

        if not raw_details or not raw_details.strip():
            return empty_record("EMPTY")
        # `text` is the flattened form, used only for whole-string tests. Clause splitting
        # must see the original: newlines and multi-space gaps are separators, and
        # normalising first would destroy them.
        text = normalise(raw_details)
        if len(text) < MIN_USEFUL_LENGTH:
            return empty_record("TOO_SHORT")

        explicit_cryo_anywhere = bool(_EXPLICIT_CRYO.search(text))

        components: list[Component] = []
        buffer_phs: list[str] = []
        standalone_ph: Optional[tuple[float, Optional[float]]] = None
        final_ph_stated = bool(_FINAL_PH.search(text))
        temp_from_text: Optional[float] = None
        protein_conc: Optional[float] = None
        drop_ratio: Optional[str] = None
        unresolved: list[str] = []
        n_reagent_clauses = 0
        kinds: list[str] = []
        # "0.1 M cacodylate, pH 6.95": a standalone pH clause directly after a buffer is
        # that buffer's pH, which is the commonest way the corpus writes it.
        previous_was_buffer = False
        ph_follows_buffer = False

        for clause in clauses(raw_details):
            kind = classify(clause)
            kinds.append(kind)

            if kind == "reagent":
                name_probe = strip_quantity(split_trailing_ph(clause)[0])
                if not name_probe or is_noise(name_probe):
                    continue
                n_reagent_clauses += 1
                explicit_here = bool(_EXPLICIT_CRYO.search(clause)) or (
                    explicit_cryo_anywhere and _EXPLICIT_CRYO.search(clause) is not None
                )
                component, buffer_ph = self.parse_component(clause, explicit_here)
                components.append(component)
                if buffer_ph:
                    buffer_phs.append(buffer_ph)
                if component.name_canonical is None:
                    unresolved.append(component.name_raw)
                previous_was_buffer = component.role == "buffer"

            elif kind == "ph":
                match = _PH_VALUE.search(clause)
                if match:
                    low = float(match.group(1))
                    high = float(match.group(2)) if match.group(2) else None
                    standalone_ph = (low, high)
                    if previous_was_buffer:
                        ph_follows_buffer = True
                previous_was_buffer = False

            elif kind == "temperature":
                temp_from_text = self._temperature(clause)

            elif kind == "protein_or_setup":
                if protein_conc is None:
                    found = _PROTEIN_CONC.search(clause)
                    if found:
                        protein_conc = float(found.group(1))
                if drop_ratio is None:
                    ratio = _DROP_RATIO.search(clause)
                    if ratio:
                        drop_ratio = f"{ratio.group(1)}:{ratio.group(2)}"

            elif kind == "reference_only":
                flags.append("defers_to_publication")

        # The drop ratio is a property of the setup, not of any one clause, and depositors
        # write it wherever they like. Falling back to a whole-string search catches the
        # cases the clause splitter has scattered.
        if drop_ratio is None:
            ratio = _DROP_RATIO.search(text)
            if ratio:
                drop_ratio = f"{ratio.group(1)}:{ratio.group(2)}"

        # -- pH attribution -------------------------------------------------
        ph_value: Optional[float] = None
        ph_source = "unstated"
        ph_is_range = False
        ph_range: Optional[tuple[Optional[float], Optional[float]]] = None

        if standalone_ph and final_ph_stated:
            ph_value, high = standalone_ph
            ph_source = "final"
            ph_is_range, ph_range = (high is not None), ((ph_value, high) if high else None)
        elif buffer_phs:
            parsed = _PH_VALUE.search(f"ph {buffer_phs[0]}")
            if parsed:
                ph_value = float(parsed.group(1))
                high = float(parsed.group(2)) if parsed.group(2) else None
                ph_is_range, ph_range = (high is not None), ((ph_value, high) if high else None)
            ph_source = "buffer"
            if len(set(buffer_phs)) > 1:
                flags.append("multiple_buffer_ph")
        elif standalone_ph:
            ph_value, high = standalone_ph
            ph_source = "buffer" if ph_follows_buffer else "unstated"
            ph_is_range, ph_range = (high is not None), ((ph_value, high) if high else None)
        elif ph_reported is not None:
            ph_value, ph_source = ph_reported, "unstated"

        if ph_value is not None and ph_reported is not None and abs(ph_value - ph_reported) > 0.5:
            flags.append("ph_conflicts_with_reported")

        # -- temperature ----------------------------------------------------
        temperature, temp_source = self._reconcile_temperature(temp_from_text, temp_k_reported)
        if temp_source == "conflict":
            flags.append("temperature_conflicts_with_reported")

        # -- confidence and discard ------------------------------------------
        resolved = sum(1 for c in components if c.name_canonical)
        resolution = resolved / n_reagent_clauses if n_reagent_clauses else 0.0
        unresolved_chars = sum(len(u) for u in unresolved)
        char_coverage = max(0.0, 1.0 - unresolved_chars / max(1, len(text)))
        confidence = round(0.7 * resolution + 0.3 * char_coverage, 3)

        if any(c.concentration_is_range for c in components):
            flags.append("concentration_range")
        if n_reagent_clauses >= 8:
            flags.append("multiple_conditions_in_field")
        if any(c.unit_inferred for c in components):
            flags.append("unit_inferred")

        discard: Optional[DiscardReason] = None
        if n_reagent_clauses == 0:
            discard = "REFERENCE_ONLY" if "defers_to_publication" in flags else (
                "METHOD_ONLY" if any(k in ("method", "temperature", "ph") for k in kinds)
                else "NON_CRYSTALLISATION_TEXT"
            )
        elif resolved == 0:
            discard = "NO_REAGENT_MATCH"
        elif confidence < 0.25:
            discard = "UNPARSEABLE_RESIDUAL"

        return ConditionRecord(
            pdb_id=pdb_id, crystal_id=crystal_id, entry_version=entry_version,
            raw_details=raw_details, method=method, diffraction_method=diffraction_method,
            temperature_k=temperature, temperature_source=temp_source,
            ph=ph_value, ph_source=ph_source, ph_is_range=ph_is_range, ph_range=ph_range,
            ph_reported=ph_reported,
            components=components,
            protein_concentration_mg_ml=protein_conc, drop_ratio=drop_ratio,
            provenance=Provenance(
                parser=PARSER_VERSION, parse_confidence=confidence, flags=sorted(set(flags)),
                n_clauses=len(kinds), n_clauses_resolved=resolved,
                unresolved_clauses=unresolved[:10],
            ),
            discard_reason=discard,
        )

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _temperature(clause: str) -> Optional[float]:
        kelvin = _TEMP_K.search(clause)
        if kelvin:
            return float(kelvin.group(1))
        celsius = _TEMP_C.search(clause)
        if celsius:
            return float(celsius.group(1)) + 273.15
        if _ROOM_TEMP.search(clause):
            return ROOM_TEMPERATURE_K
        bare = _TEMP_BARE.search(clause)
        if bare:
            value = float(bare.group(1))
            # No unit: anything plausible as Celsius is Celsius, anything above 250 is
            # already Kelvin. Crystallisation happens between roughly 273 K and 313 K.
            return value + 273.15 if value <= 45 else value
        return None

    @staticmethod
    def _reconcile_temperature(
        from_text: Optional[float], reported: Optional[float]
    ) -> tuple[Optional[float], Optional[str]]:
        if from_text is not None and reported is not None:
            if abs(from_text - reported) <= 2.0:
                return reported, "both_agree"
            return reported, "conflict"      # the structured field wins, the clash is flagged
        if reported is not None:
            return reported, "reported"
        if from_text is not None:
            return from_text, "text"
        return None, None
