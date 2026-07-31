"""Recognise clauses that are not reagents at all.

Measured on the 125,970 unresolved components: **17.5% are not chemistry**. They are method
text ("crystal obtained by streak-seeding", 367; "batch", 358; "small tubes", 255), unnamed
proteins and ligands ("protein", 799; "inhibitor", 382), screen references ("buffer system 3"),
and bare splitter fragments ("na", "k").

Why this matters beyond tidiness: these were counted in the denominator of the reagent
resolution rate, so the parser was being scored as having *failed to resolve* text that contains
no reagent to resolve. Excluding them moves measured resolution from 79.1% to 82.1% without
changing a single parse. R1's 85% gate is set against that denominator, so getting it right is
the difference between measuring the parser and measuring an artefact.

It also matters for the fine-tuned model. Training it to emit a reagent name for "small tubes"
teaches it to invent chemistry, which is the one failure mode that makes structured output
worse than no output.

**Deliberately conservative.** A false positive here silently deletes a real reagent, which is
far worse than leaving an unresolved component in place: the unresolved case is visible in the
coverage report, the deleted case is not. So every pattern must be anchored or specific, and
anything that carries a concentration is treated as a reagent regardless of what it looks like,
because a quantity is strong evidence that a depositor was naming a substance.
"""

from __future__ import annotations

import re
from typing import Optional

# Method, protocol and apparatus text. The largest slice at 13.4% of unresolved. Anchored on
# verbs and apparatus nouns that cannot appear in a reagent name.
_METHOD = re.compile(
    r"\b(?:streak[- ]?seed\w*|micro[- ]?seed\w*|macro[- ]?seed\w*|seeded|seeding|"
    r"hanging[- ]drop|sitting[- ]drop|vapou?r[- ]diffusion|free[- ]interface|"
    r"micro[- ]?batch|batch method|counter[- ]?diffusion|liquid[- ]liquid|"
    r"equilibrat\w+|incubat\w+|dialy\w+|centrifug\w+|pipett\w+|"
    r"reproducibility|was improved|were improved|obtained by|grown (?:by|in|at|from)|"
    r"crystals? (?:were|was|appeared|grew|grown|formed|obtained)|"
    r"small tubes?|capillar\w+|siliconi[sz]ed|coverslip|"
    r"reservoir volume|drop (?:size|volume|ratio)|mixed (?:with|in)|"
    # Apparatus and cryo-protocol notes that reached the reagent audit as if they were
    # substances: "ul reservoir" (137), "cryo: direct" (142).
    r"[muµ]l reservoir|reservoir$|cryo\s*:|cryo-?protect(?:ion|ant)? ?:|"
    r"flash[- ]?(?:cool|froze|frozen)|liquid nitrogen|direct(?:ly)? (?:frozen|from))",
    re.I)

# A clause that is nothing but a method noun. "batch" alone is a method; "batch" inside
# "ammonium sulfate batch 3" is not the whole clause and is left alone.
_METHOD_ONLY = re.compile(
    r"^(?:batch|vapou?r diffusion|hanging drop|sitting drop|microbatch|micro[- ]batch|"
    r"dialysis|free interface diffusion|counter[- ]?diffusion|lcp|"
    r"lipidic cubic phase|in meso|under oil|oil|seeding|streak seeding|"
    r"cryo|cryoprotectant|cryoprotection|frozen|flash[- ]?(?:cooled|frozen))$", re.I)

# Unnamed protein, ligand or macromolecule. The depositor named a role, not a substance, so
# there is nothing to look up. Anchored to the whole clause: "inhibitor" alone is unnamable,
# but "trypsin inhibitor" is a real substance.
_UNNAMED = re.compile(
    r"^(?:the )?(?:protein|proteins|inhibitor|inhibitors|compound|compounds|ligand|ligands|"
    r"substrate|substrates|peptide|peptides|complex|enzyme|antibody|antibodies|fab|fab fragment|"
    r"dna|rna|nucleic acid|oligonucleotide|nucleotide|cofactor|cofactors|"
    r"analog|analogue|derivative|sample|solution|mixture|reagent|additive|"
    r"macromolecule|substrate analog(?:ue)?|product|metabolite)$", re.I)

# A reference to a commercial screen or an internal condition number rather than a composition.
_SCREEN_REF = re.compile(
    r"\b(?:buffer system \d|condition (?:no\.?|number|#)? ?\d|screen(?:ing)? (?:kit|solution)|"
    r"crystal ?screen(?: (?:i|ii|hf|lite|\d))?|index (?:hf|screen)?|salt ?rx|"
    r"pact ?(?:premier|suite)?|jcsg[- ]?(?:plus|core|\+)?|wizard (?:i|ii|iii|iv|\d)|"
    r"structure screen|morpheus|natrix|nextal|classics (?:ii|lite|suite)?|"
    r"grid screen|proplex|midas|stura|clear ?strategy|"
    r"(?:hampton|qiagen|molecular dimensions|emerald|rigaku|jena) )", re.I)

# A defer-to-the-literature clause. Negligible corpus-wide (107 records) but unambiguous.
_REFERENCE = re.compile(
    r"(?:see (?:the )?(?:publication|paper|reference|manuscript|article|text|methods?)|"
    r"as (?:described|published|reported) (?:in|by|previously)|"
    r"\bet al\b|\bibid\b|reference \d|doi:|pubmed)", re.I)

# A single element symbol, ion fragment or stray token left behind by clause splitting. These
# are splitter artefacts, not reagents: "na" on its own names no salt because the counter-ion
# is what makes it one. Whitelisted exceptions are the real one- and two-letter reagents.
_FRAGMENT = re.compile(
    r"^(?:na|k|ca|mg|li|cs|rb|ba|sr|zn|mn|fe|ni|co|cu|cd|al|nh4|cl|br|i|f|"
    # Spelled-out counter-ions with no anion, e.g. "sodium" alone (192 components). A cation
    # without its partner names no salt, exactly as the symbol form does not.
    r"sodium|potassium|lithium|caesium|cesium|rubidium|ammonium|magnesium|calcium|barium|"
    r"strontium|zinc|manganese|nickel|cobalt|copper|cadmium|iron|aluminium|aluminum|"
    r"so4|po4|no3|oac|ac|cit|tart|mal|"
    r"ph|of|and|or|in|at|on|the|a|an|with|to|as|by|from|for|is|are|was|were|it|its|"
    r"no|none|n/?a|nil|null|unknown|unspecified|not (?:given|stated|specified|available)|"
    r"[-–—.,;:+/\\()\[\]]+|\d+(?:\.\d+)?)$", re.I)

# Real reagents short enough to be caught by _FRAGMENT. Checked first.
_SHORT_REAL = {"mpd", "peg", "dtt", "tris", "hepes", "mes", "bis", "edta", "egta", "dmso",
               "dtt", "tcep", "bme", "gol", "urea", "spg", "mme", "nad", "fad", "atp", "adp",
               "amp", "gtp", "gdp", "plp", "plp", "sam", "coa", "nadp", "imidazole"}

REASONS = ("method_text", "unnamed_macromolecule", "screen_reference",
           "publication_reference", "splitter_fragment")


def classify(name: str, has_quantity: bool = False) -> Optional[str]:
    """Return why this clause is not a reagent, or None if it might be one.

    `has_quantity` vetoes only the *substring* rule. That rule matches a method phrase anywhere
    in the clause, so it can fire on "0.2 M sodium chloride was mixed with", where real chemistry
    sits beside the method text and must not be discarded. A concentration is good evidence that
    a substance is being named, so it wins there.

    It deliberately does **not** veto the anchored whole-clause rules. "1 mM inhibitor" carries a
    concentration and still names no substance: the depositor gave a role, not a compound, and no
    lexicon entry can ever match it. Since the pattern consumes the entire clause in those cases,
    there is no chemistry beside it to protect, so the veto would only suppress a correct answer.
    """
    text = (name or "").strip()
    if not text:
        return None
    lowered = text.lower()

    # Genuine short reagents before the fragment rule, or "peg" and "mpd" would be discarded.
    if lowered in _SHORT_REAL:
        return None

    # Anchored rules: the pattern is the whole clause, so a quantity changes nothing.
    if _FRAGMENT.match(lowered):
        return "splitter_fragment"
    if _UNNAMED.match(lowered):
        return "unnamed_macromolecule"
    if _METHOD_ONLY.match(lowered):
        return "method_text"
    if _REFERENCE.search(lowered):
        return "publication_reference"
    if _SCREEN_REF.search(lowered):
        return "screen_reference"

    # Substring rule: only safe when no concentration suggests a substance is being named.
    if not has_quantity and _METHOD.search(lowered):
        return "method_text"
    return None
