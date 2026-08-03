"""The deterministic rule parser: free text to schema-valid components.

Spec 5.3: write the deterministic parser first, and expect it to plateau around 75%. Its
output bootstraps the labels for the small language model that handles the residual, and it
remains the parser of record for everything it is confident about.

The three judgement calls it makes, all of them recorded in the output rather than hidden:

  unit inference    ~80% of percentage concentrations carry no w/v or v/v marker. Identified
                    from the reagent's chemistry and flagged with `unit_inferred`.
  pH attribution    a pH attached to a buffer clause is the buffer's; a standalone "pH x"
                    is the final pH only when the text says so. Anything else is `unstated`
                    rather than quietly assumed.
  cryo attribution  only ~2.2% of entries name a cryoprotectant. Reagents that are merely
                    capable of the role, at cryo-scale concentrations, are labelled
                    `inferred` and never confused with the explicit cases.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

import regex as re

from . import quantity, scope
from .lexicon import Lexicon, Reagent
# Aliased: `.text` already exports a `classify` for a different job (condition-level text kind).
from .noncomponent import classify as classify_non_component
from .prose import strip_prose
from .schema import Component, ConditionRecord, DiscardReason, Provenance
from .text import (classify, clauses_detailed, is_noise, normalise,
                   split_trailing_ph, strip_quantity, tidy_name)

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

    def _head_is_reagent(self, head: str) -> bool:
        """Does this clause head name a reagent we know?

        The gate on truncating a clause at its trailing setup prose. `text` cannot ask this
        itself, being the module the lexicon is built on, so it asks the caller instead.
        Without the gate the cut fires on shape alone and yields "ul", "protein at 10" and
        "nacl was": 3,752 unidentified heads against 221 real ones over 60,000 conditions.
        """
        name = tidy_name(strip_quantity(split_trailing_ph(head)[0]))
        return bool(name) and normalise(name) in self.index

    # -- components --------------------------------------------------------

    def parse_component(self, clause: str,
                        explicit_cryo: bool) -> tuple[Component, Optional[str], bool]:
        """Parse one reagent clause.

        Returns the component, any buffer pH found on it, and whether an impossible
        concentration was dropped, which the caller records as a flag on the record: a rule that
        deletes a stated amount must never do so silently.
        """
        component, buffer_ph, implausible = self._parse_component(clause, explicit_cryo)
        if component.name_canonical is not None or component.role == "not_a_component":
            return component, buffer_ph, implausible

        # The clause did not identify. Depositors write sentences, so it may be chemistry wrapped
        # in narrative: "crystal conditions were 100 mm bis-tris propane". Retried with the prose
        # removed, and the retry is kept only if it actually identifies, so a working parse can
        # never be made worse. Measured: 13.7% of unidentified components are longer than 40
        # characters and 7.1% contain a verb.
        stripped = strip_prose(clause)
        if stripped:
            retried, retried_ph, retried_implausible = self._parse_component(
                stripped, explicit_cryo)
            if retried.name_canonical is not None:
                return retried, retried_ph, retried_implausible
        return component, buffer_ph, implausible

    def _parse_component(self, clause: str,
                         explicit_cryo: bool) -> tuple[Component, Optional[str], bool]:
        body, attached_ph = split_trailing_ph(clause)
        qty = quantity.extract(body)
        name = strip_quantity(body)
        reagent = self.index.get(normalise(name)) if name else None

        # "0.1 M CAPS/ Sodium hydroxide": one buffer titrated with one base. Written with spaces
        # around the slash the splitter separates them and `scope.is_buffer_titrant` catches the
        # second half; written without, the clause stays whole and identifies to nothing -- so
        # the buffer is lost along with the titrant, which is the worse of the two failures.
        #
        # Only ever a retry after the full name has failed. `sodium acetate/acetic acid` is a
        # lexicon entry in its own right on some spellings, and a name that already resolves is
        # never second-guessed.
        if reagent is None and name:
            without = scope.strip_titrant_suffix(name)
            if without:
                candidate = self.index.get(normalise(without))
                if candidate is not None:
                    reagent, name = candidate, without

        # "hepes 7.5", "mes 6.5": a buffer named with a bare pH and no "pH" token. Retrying
        # without the trailing number identifies it, and the number is kept as the buffer pH.
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

        # **An impossible amount is dropped, the reagent is kept.** "10 M ZnCl2" (4IBR) and
        # "3000 M Sodium malonate dibasic" (7DXZ) are what the depositions say, and reading them
        # faithfully is right; asserting them downstream is not. Nulling the amount routes the
        # condition to the same `no_amount` outcome as a reagent that never stated one, which is
        # the honest description: we know what is in the drop, not how much.
        implausible = quantity.is_implausible(qty.value, unit)
        if implausible:
            qty = replace(qty, value=None, low=None, high=None, is_range=False)
            unit, inferred = None, False

        role = _role_for(reagent, self.lexicon)
        evidence = _cryo_evidence(reagent, qty, unit, explicit_cryo)
        if evidence is not None:
            role = "cryo"

        # Only asked when the lexicon found nothing. A clause that identified to a reagent is a
        # reagent, whatever else its wording looks like, so the classifier never gets to
        # second-guess a positive match.
        non_component_reason = None
        if reagent is None:
            non_component_reason = classify_non_component(
                name or clause, has_quantity=qty.value is not None)
            if non_component_reason is not None:
                role = "not_a_component"

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
            # A confident non-reagent is a correct parse, not a failed one: the text genuinely
            # holds no chemistry. Scoring it 0.2 like an unrecognised reagent would drag record
            # confidence down for getting something right.
            parse_confidence=1.0 if (reagent or non_component_reason) else 0.2,
            non_component_reason=non_component_reason,
        )
        buffer_ph = attached_ph if (reagent and reagent.chem_class == "buffer") else None
        return component, buffer_ph, implausible

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
        unidentified: list[str] = []
        n_reagent_clauses = 0
        kinds: list[str] = []
        # "0.1 M cacodylate, pH 6.95": a standalone pH clause directly after a buffer is
        # that buffer's pH, which is the commonest way the corpus writes it.
        previous_was_buffer = False
        ph_follows_buffer = False

        # Passages that describe the protein sample or a soak rather than the drop. Empty for
        # almost every deposition; see `parse.scope` for why this is a span and not a blocklist.
        scope_ranges = scope.spans(raw_details)
        cursor = scope.Cursor(raw_details)

        for clause, bracket_depth in clauses_detailed(raw_details, self._head_is_reagent):
            clause_at = cursor.locate(clause)
            clause_scope = scope.role_at(clause_at, scope_ranges)
            # Inside an unclosed bracket AND carrying no amount: the constituent list of the
            # reagent that opened it. "PEG Smear Broad (PEG 400, PEG 600, ...)" is one
            # reagent, and splitting it produced nine phantom PEGs.
            #
            # The quantity test is what makes this safe. Brackets are used for both purposes:
            # "reservoir solution (20% PEG 3350, 0.2 M NaCl)" is the condition itself, and
            # skipping on depth alone discarded 1,893 records that had their reagents written
            # that way.
            if bracket_depth > 0 and not quantity.extract(clause).found:
                flags.append("parenthetical_enumeration")
                continue
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
                component, buffer_ph, implausible = self.parse_component(
                    clause, explicit_here)
                if implausible:
                    flags.append("implausible_concentration")

                # **"0.1 M CAPS/ Sodium hydroxide pH 10.5" is one buffer, not two reagents.** The
                # splitter breaks on the slash and hands back a bare `sodium hydroxide` with no
                # amount, which then enters the dataset as an additive the depositor never added.
                #
                # The pH is deliberately *kept* and re-attributed. It is the buffer system's pH,
                # stated on the half the splitter put it next to, so discarding it with the
                # titrant would lose the one number in the clause that describes the condition.
                if (component.name_canonical in scope.TITRANTS
                        and scope.is_buffer_titrant(raw_details, clause_at,
                                                    component.concentration is not None)):
                    component = component.model_copy(update={
                        "role": "not_a_component",
                        "name_canonical": None, "chem_class": None, "buffer_pka": None,
                        "concentration": None, "unit": None,
                        "non_component_reason": "buffer_titrant",
                    })
                    flags.append("buffer_titrant")
                    # Read off the clause rather than taken from `buffer_ph`, which is only
                    # populated for a buffer-class reagent -- and sodium hydroxide is an
                    # additive, so the pH of "CAPS/ Sodium hydroxide pH 10.5" arrives here as
                    # None however plainly the text states it. Credited to the buffer on the
                    # other side of the slash, which is whose pH it is.
                    titrant_ph = split_trailing_ph(clause)[1]
                    if titrant_ph and previous_was_buffer:
                        buffer_phs.append(titrant_ph)
                    buffer_ph = None

                # **The reagent is kept, only its scope is corrected.** A protein storage buffer
                # is read exactly as before -- name, amount, unit, all of it -- and the role
                # records that the crystal did not grow in it. Dropping the component instead
                # would lose a correct reading and leave the record looking as though the parser
                # had failed on text it understood perfectly.
                #
                # Overrides whatever chemistry role was assigned, including `cryo`: a glycerol in
                # a storage buffer is not a cryoprotectant either. Never overrides
                # `not_a_component`, which is a stronger statement -- there was no reagent at all
                # -- and would be weakened by claiming the non-reagent belongs to a buffer.
                #
                # **Only when the reagent identified, and this is a guard against flattering the
                # metrics.** An unidentified clause inside a scope span is still a reagent the
                # lexicon failed on, and marking it out-of-scope would quietly move it out of the
                # identification denominator: the corpus would score better for parsing worse.
                # It costs a little precision -- a genuinely out-of-scope unidentified reagent
                # keeps `unknown` -- and that is the right way round, because `unknown` overstates
                # nothing while a false `protein_buffer` hides a real failure.
                if clause_scope and component.name_canonical is not None:
                    component = component.model_copy(
                        update={"role": clause_scope, "cryo_evidence": None})
                    flags.append(f"scope_{clause_scope}")
                    buffer_ph = None            # a storage buffer's pH is not the drop's

                components.append(component)
                if buffer_ph:
                    buffer_phs.append(buffer_ph)
                if component.name_canonical is None:
                    unidentified.append(component.name_raw)
                previous_was_buffer = component.role == "buffer"

            elif kind == "ph":
                # A pH inside a protein section belongs to the storage buffer, not the drop.
                # Leaving `standalone_ph` unset routes the record to `ph_source = unstated`,
                # which is the honest answer when the only stated pH was measured on something
                # else.
                #
                # **Protein sections only, deliberately not soaks.** A protein section is always
                # followed by condition text, so there is somewhere better for the pH to come
                # from. A soak span runs to the end of the deposition and therefore swallows the
                # trailing `pH 5.5, VAPOR DIFFUSION, temperature 293K` block that the deposition
                # system appends -- which describes the record, not the soak. 2IH1 lost a correct
                # pH that way before this was narrowed.
                match = _PH_VALUE.search(clause)
                if match and clause_scope != "protein_buffer":
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
        identified = sum(1 for c in components if c.name_canonical)
        # Clauses confidently identified as containing no chemistry are removed from the
        # denominator, exactly as they are from the corpus-level identification rate. Leaving them in
        # capped record confidence below 1.0 for any record mentioning its own method: 2,539
        # records with every reagent identified still scored a median 0.735 and a maximum 0.925,
        # purely for containing a phrase like "streak seeded". That silently excluded every one
        # of them from the fine-tuning set, so the model saw no example of the non-reagent verdict
        # and had no way to learn it.
        n_non_component = sum(1 for c in components if c.role == "not_a_component")
        n_chemistry_clauses = max(0, n_reagent_clauses - n_non_component)
        identification = identified / n_chemistry_clauses if n_chemistry_clauses else 0.0
        unidentified_chars = sum(len(u) for u in unidentified)
        char_coverage = max(0.0, 1.0 - unidentified_chars / max(1, len(text)))
        confidence = round(0.7 * identification + 0.3 * char_coverage, 3)

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
        elif n_chemistry_clauses == 0:
            # Clauses were found but every one of them is method text, a screen reference or an
            # unnamed macromolecule. That is method-only, not a failure to match a reagent: there
            # was no reagent named to match.
            discard = "METHOD_ONLY"
        elif identified == 0:
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
                n_clauses=len(kinds), n_clauses_identified=identified,
                unidentified_clauses=unidentified[:10],
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
