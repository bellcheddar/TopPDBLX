"""Shared text normalisation for crystallisation condition strings.

Used by the WP2 candidate miner and, later, by the WP3 rule parser: both must agree on
what a clause is and what a reagent name looks like, or the lexicon keys will not match
what the parser produces.

Every transformation here is deliberately conservative. Aggressive normalisation merges
chemically distinct reagents, which is worse than leaving a few duplicate candidates for
the curator to map by hand.
"""

from __future__ import annotations

import unicodedata
from typing import Callable

import regex as re

# Unicode dashes and lookalikes. Rare in this corpus (0 of 600 in the planning probe) but
# they break naive numeric regexes when they do appear.
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")

_NUMBER = r"\d+(?:[.,]\d+)?"
_UNIT_BODY = r"""(?:
      %\s*\(?\s*(?:w\s*/\s*v|v\s*/\s*v|w\s*/\s*w)\s*\)?
    | %
    | m\b | mm\b | µm\b | um\b | nm\b | mol/l\b
    | mg\s*/\s*ml\b | g\s*/\s*l\b | mg\s*/\s*l\b
)"""
_UNIT = _UNIT_BODY + "?"        # optional: "1.7 to 2.1 ammonium sulfate" omits the unit
_UNIT_REQUIRED = _UNIT_BODY
# The unit may appear on the first endpoint as well as the second ("28% to 32% PEG 3350").
# It is only allowed there when a separator follows, so an explicit "30% (w/v)" keeps its
# marker instead of having the % consumed here.
_RANGE = (rf"{_NUMBER}(?:\s*{_UNIT_BODY}(?=\s*(?:-|to|–)\s*\d))?"
          rf"(?:\s*(?:-|to|–)\s*{_NUMBER})?")

_SPLIT = re.compile(r"""
    \s*(?:
        [;\n]
      | \.\s+(?=[a-z0-9])  # "protease 2mg/ml. precipitant 0.7M NaCl": a sentence break.
                          # Requires whitespace after the stop, so "0.1" and "2.4M" are safe
      | ,(?!\d)          # a comma not followed by a digit, so "peg 6,000" stays whole
      | \s--+\s          # "bis-tris ph 7.0 -- 30% peg3350": a real separator in this corpus,
                         # spaced so it cannot eat the hyphen in "bis-tris" or "tris-hcl"
      | \s\+\s
      | \s+and\s+
      | \s+plus\s+
      | \s+/\s+          # "0.98 M sodium malonate pH 7.0 / 0.02 M citric acid": a titration
                         # pair. Spaces are required on both sides, so "w/v", "mg/ml",
                         # "hepes/naoh" and "na/k phosphate" are untouched
      | \s+in\s+(?=\d)   # "50mM CaCl2 in 0.1M cacodylate": only before a digit, so
                         # "crystals grown in sitting drops" is left alone
      | (?<=\bph\s{0,2}\d{1,2}(?:\.\d+)?)\s+(?=\d)
                         # "25mM Tris/HCl pH 7.5 100mM NaCl": a pH declaration followed by a
                         # further concentration ends the clause. Without this the whole tail
                         # becomes one unidentifiable reagent name and the NaCl is lost
      | \ \ +(?=\d)      # "glycol 6,000  100 mM citrate": depositors separate components
                         # with extra spaces. Requiring a following digit keeps it safe,
                         # since a new component almost always opens with a concentration
      | \s+(?=(?:crystalli[sz]ation|reservoir|well|mother|protein|precipitant|drop|screen
                |condition)
             \s*(?:solution|liquor|buffer|mix|conditions?)?\s*:)
                         # "50 MM NACL CRYSTALLIZATION BUFFER: 10% PEG 8000": a section label
                         # written mid-string with no comma before it. Break in front of the
                         # label so neither the component before nor the one after is swallowed
    )\s*
""", re.VERBOSE)

_LEADING_QTY = re.compile(rf"^\s*{_RANGE}\s*{_UNIT}\s*(?:of\s+)?", re.VERBOSE)

# The trailing form requires an explicit unit, unlike the leading form. Without that,
# "peg 8000" (a name with no stated concentration) has its molecular weight stripped as if
# it were an amount, leaving a bare "peg" and destroying the reagent's identity. A trailing
# bare number is far more often part of the name than a concentration.
_TRAILING_QTY = re.compile(rf"\s*{_RANGE}\s*{_UNIT_REQUIRED}\s*$", re.VERBOSE)

# A trailing pH, with or without a range or brackets: "hepes ph 7.5", "mes ph 6.0-6.5",
# "tris, ph=8", "hepes (ph 7.5)". Splitting this off collapses thousands of spurious
# candidate variants. The bracketed form alone accounts for over a thousand clauses.
_TRAILING_PH = re.compile(rf"""
    \s*[,;]?\s*\(?\s*(?:at\s+|final\s+|buffer\s+)?ph\s*[:=]?\s*({_RANGE})\s*\)?\s*$
""", re.VERBOSE)

_METHOD = re.compile(r"""
    vapou?r\s+diffusion | hanging\s+drop | sitting\s+drop | sandwich\s+drop
  | microbatch | micro-?seeding | macro-?seeding | batch\s+method | free\s+interface
  | dialysis | evaporation | liquid\s+diffusion | counter\s*-?\s*diffusion
  | lipidic\s+cubic\s+phase | \blcp\b | nanodrop | crystalli[sz]ation\s+was
  | crystals?\s+(?:were|was)
""", re.VERBOSE)

_TEMPERATURE = re.compile(r"""
    ^\s*(?:at\s+)?temperature\b | ^\s*\d+(?:\.\d+)?\s*(?:k|kelvin)\s*$
  | ^\s*\d+(?:\.\d+)?\s*(?:deg(?:rees)?\s*)?c(?:elsius)?\s*$
  | room\s+temperature
""", re.VERBOSE)

_PH_CLAUSE = re.compile(r"^\s*(?:final\s+|buffer\s+)?ph\b|^\s*at\s+ph\b")

# Clauses that defer to the literature instead of stating a condition. Spec 5.1 lists this
# as a discard reason; the WP3 taxonomy uses the REFERENCE_ONLY code for it.
_REFERENCE = re.compile(r"""
    see\s+(?:also\s+)?(?:pmid|publication|reference|paper|literature|the\s+paper)
  | as\s+(?:described|published|reported)\s+in
  | \bpmid\b | \bdoi\b
""", re.VERBOSE)

_PROTEIN = re.compile(r"""
    protein\s+(?:concentration|solution|conc) | mg\s*/\s*ml\s*(?:protein)?
  | (?:reservoir|well|mother)\s+(?:solution|liquor)
  | drop\s+(?:ratio|volume) | \bmicroliters?\b | \bul\b\s+of | \bµl\b
  | mixed\s+(?:\d\s*:\s*\d|1:1|in\s+a?\s*\d) | \bratio\b | equal\s+volumes?
  | mixed\s+[^,;]{0,40}?\bwith\b | consisting\s+of | \bdiluted\b
  | total\s+volume | \bnl\b
""", re.VERBOSE)

_NOISE = re.compile(r"""
    ^\s*(?:the|a|an|in|with|using|containing|solution|buffer(?:ed)?|about|approx\w*|final|
          equal|volume|ratio|mixed|mixture|added|and|or|of|at|by|to|from|was|were|
          crystals?|crystalli[sz]ation|condition|well|reservoir|drop)\s*$
""", re.VERBOSE)

# "peg3350" and "peg 3350" are the same reagent written two ways, and both are common.
# Only these known polymer prefixes are split on the letter/digit boundary: a blanket rule
# would wreck chemical formulae, turning "mgcl2" into "mgcl 2" and "k2hpo4" into nonsense.
_POLYMER_PREFIX = re.compile(r"^(m?peg|peg-?mme|mme|pei|ppg)\s*-?\s*(\d{3,6})\b")


# Conjunctions and prepositions left at the head of a clause by splitting. "and 172 mM
# ammonium nitrate" must become "172 mM ammonium nitrate" or the reagent never identifies.
_LEADING_CONJUNCTION = re.compile(
    r"^(?:and|plus|with|in|of|containing|at"
    # Descriptive labels depositors put in front of the reagent itself:
    # "precipitant 0.7M NaCl", "reservoir 20% PEG 3350".
    r"|precipitants?|crystallant|reservoir|mother\s+liquor|well\s+solution)\s+")

# The same labels again, but as a *headed section* ending in a colon: "CRYSTALLIZATION BUFFER:
# 10% PEG 8000, 0.1 M HEPES". Depositors write these mid-string with no comma in front, so the
# label glues the preceding component to the following one and both are lost inside a name that
# identifies to nothing. 3ZY1 lost its PEG 8000 exactly this way, found by the 2026-08-01 audit.
# **"protein" is absent on purpose.** A break is made in front of "PROTEIN SOLUTION:" so it
# cannot glue itself to the preceding component, but the label is then left in place: what
# follows it is the protein's own buffer, not the crystallisation condition, and keeping the
# label is what lets `classify` reject the clause.
_SECTION_LABEL = r"""
    (?:crystalli[sz]ation|reservoir|well|mother|precipitant|drop|screen|condition)
    \s* (?:solution|liquor|buffer|mix|conditions?)? \s* :
"""
_LEADING_SECTION = re.compile(rf"^\s*{_SECTION_LABEL}\s*", re.VERBOSE)


def normalise(text: str) -> str:
    """NFKC, unify dashes, collapse all whitespace, lowercase.

    Used for lexicon keys and name comparison, where whitespace is never meaningful.
    Clause splitting uses `_prepare` instead, which preserves the whitespace it needs.
    """
    text = unicodedata.normalize("NFKC", text).translate(_DASHES)
    return re.sub(r"\s+", " ", text).strip().lower()


def _prepare(text: str) -> str:
    """Normalise without destroying the whitespace that separates clauses.

    Newlines and runs of spaces are both load-bearing: depositors put one component per
    line, or separate them with extra spaces, and collapsing either before the split turns
    a four-component condition into one unparseable string.
    """
    return unicodedata.normalize("NFKC", text).translate(_DASHES).strip().lower()


def drop_unclosed_tail(text: str) -> str:
    """Remove a trailing bracket that never closes, with whatever follows it.

    "22.5% (v/v) PEG Smear Broad (PEG 400" keeps its balanced "(v/v)" but the dangling
    "(PEG 400" is the head of a constituent list the splitter cut mid-way. Left in place it
    becomes part of the reagent name and nothing identifies.
    """
    if text.count("(") <= text.count(")"):
        return text
    depth = 0
    cut = None
    for position, char in enumerate(text):
        if char == "(":
            if depth == 0:
                cut = position
            depth += 1
        elif char == ")":
            depth -= 1
            if depth <= 0:
                depth = 0
                cut = None
    return text[:cut].strip() if cut is not None else text


def trim_unmatched_parens(text: str) -> str:
    """Drop brackets left dangling by splitting, without touching balanced formulae.

    "(0.07 m citric acid" and "ph 3.4)" are fragments of a bracketed group the splitter
    broke apart; a leading bracket there blocks quantity stripping entirely. "(nh4)2so4" is
    balanced and load-bearing, so it survives untouched.
    """
    while text.count("(") > text.count(")") and text.startswith("("):
        text = text[1:].strip()
    while text.count(")") > text.count("(") and text.endswith(")"):
        text = text[:-1].strip()
    return text


def clauses_detailed(
    text: str, is_reagent: "Callable[[str], bool] | None" = None,
) -> list[tuple[str, int]]:
    """Split into clauses, each with the bracket depth it sits at.

    `is_reagent` decides whether a clause carrying trailing setup prose may be truncated to
    its head. Callers that hold the lexicon should pass one; without it no clause is cut, which
    is the behaviour every caller had before.

    Depth matters because depositors enumerate the constituents of a premix in brackets:

        22.5% (v/v) PEG Smear Broad (PEG 400, PEG 600, PEG 1000, PEG 2000, ...)

    Splitting on the commas turns one reagent at 22.5% into nine reagents at no
    concentration. A clause inside an unclosed bracket is explanatory text about the reagent
    that opened it, not a component of the condition, and the depth is the only thing that
    distinguishes the two.
    """
    out: list[tuple[str, int]] = []
    depth = 0
    for part in _SPLIT.split(_prepare(text)):
        if not part:
            continue
        collapsed = re.sub(r"\s+", " ", part).strip()
        entry_depth = depth
        depth += collapsed.count("(") - collapsed.count(")")
        depth = max(0, depth)
        cleaned = trim_unmatched_parens(
            drop_unclosed_tail(
                _LEADING_CONJUNCTION.sub("", _LEADING_SECTION.sub("", collapsed))))
        if not cleaned:
            continue
        # **A reagent followed by setup prose keeps its reagent.** "10% 1-BUTANOL mixed with the
        # 10 mg/mL protein stock at 1:1 ratio" matches _PROTEIN, so `classify` calls the whole
        # clause protein_or_setup and the butanol is discarded with it. Cut the prose off and
        # keep the head. 7O5Q and 7NRJ both lost their butanol this way, found by the
        # 2026-08-01 audit. The prose itself is dropped rather than emitted as a clause of its
        # own: it was never a component, and emitting it made one.
        out.append((_split_off_setup_prose(cleaned, is_reagent), entry_depth))
    return out


def _split_off_setup_prose(clause: str, is_reagent: "Callable[[str], bool] | None") -> str:
    """Cut trailing protein/setup prose off a clause that starts with a real component.

    Only cuts when the prose begins *after* something quantified, so a clause that is setup
    text throughout is left whole for `classify` to reject as it always did.

    **The head must be a reagent the caller recognises, and that gate is not optional.**
    Measured on 60,000 conditions, cutting on shape alone produced 3,752 unidentified heads
    against 221 real ones: "ul", "protein at 10", "nacl was", "set up in a 1:1". A digit and a
    letter are not enough of a test, and this module cannot consult the lexicon itself because
    the lexicon is built on top of it. So the caller supplies the predicate, and without one
    nothing is cut.
    """
    if is_reagent is None:
        return clause
    match = _PROTEIN.search(clause)
    if not match or match.start() == 0:
        return clause
    head = clause[:match.start()].strip(" ,;:")
    # The head has to look like a component in its own right: an amount *and* a name. Without
    # the digit check "mother liquor mixed with protein" would split into two pieces of prose;
    # without the letter check "12-15 mg/ml" would split into a bare "12-15" that classifies as
    # a reagent, which is how a protein concentration turns into a component.
    if not head or not re.search(r"\d", head) or not re.search(r"[a-z]", head) or is_noise(head):
        return clause
    return head if is_reagent(head) else clause


def clauses(text: str, is_reagent: "Callable[[str], bool] | None" = None) -> list[str]:
    """Split into component clauses, tidying each only after the split."""
    return [clause for clause, _ in clauses_detailed(text, is_reagent)]


def split_trailing_ph(clause: str) -> tuple[str, str | None]:
    """Separate a trailing pH from a reagent clause.

    "hepes ph 7.5" -> ("hepes", "7.5"). Without this, every buffer appears once per pH
    value in the candidate ranking and the tail looks far worse than it is.
    """
    # Applied repeatedly: "0.1 M HEPES pH 7.5 (final pH 7.4)" carries two, and stripping only
    # the outer one leaves "hepes ph 7.5" as the reagent name, which identifies to nothing.
    found = None
    while True:
        match = _TRAILING_PH.search(clause)
        if not match:
            break
        found = found or match.group(1)
        stripped = clause[:match.start()].strip()
        if stripped == clause:
            break
        clause = stripped
    return clause, found


def _unwrap(name: str) -> str:
    """Remove a wrapping paren pair, but never break a chemical formula.

    "(nh4)2so4" keeps its parens: they are balanced and load-bearing. Only a pair that
    encloses the whole string is stripped.
    """
    while len(name) > 2 and name.startswith("(") and name.endswith(")"):
        depth = 0
        for position, char in enumerate(name):
            depth += (char == "(") - (char == ")")
            if depth == 0 and position < len(name) - 1:
                return name          # the opening paren closes early: not a wrapper
        name = name[1:-1].strip()
    return name


# Hydration state is a property of the solid reagent in the bottle, not of the condition:
# "calcium chloride dihydrate" and "calcium chloride" are the same species in solution.
# Stripping it generically collapses a long tail of vendor and depositor spellings.
_HYDRATE = re.compile(r"""
    \s+(?:mono|di|tri|tetra|penta|hexa|hepta|octa|deca)?hydrate\b
  | \s+anhydrous\b
  | \s+\d+\s*\.?\s*h2o\b
""", re.VERBOSE)

# "nickel(ii) chloride" and "iron(iii) chloride": the oxidation state is implied by the
# formula and never distinguishes two reagents in a crystallisation screen.
_OXIDATION_STATE = re.compile(r"\s*\((?:i{1,3}|iv|v|vi)\)\s*", re.IGNORECASE)

# Trademark and registered marks survive PDF extraction as literal text ("tacsimatetm").
_TRADEMARK = re.compile(r"\s*(?:™|®|©|\(tm\)|\(r\))\s*", re.IGNORECASE)
_TRAILING_TM = re.compile(r"(?<=[a-z])tm\b")


def tidy_name(name: str) -> str:
    """Light canonicalisation of a candidate reagent name."""
    name = name.strip().strip(" .:;,-")
    name = _TRADEMARK.sub(" ", name)
    name = _TRAILING_TM.sub("", name)
    name = _OXIDATION_STATE.sub(" ", name)
    name = _HYDRATE.sub("", name)
    name = _unwrap(name)
    # "peg 3350)" and "(peg 3350": a stray unmatched paren left by clause splitting.
    name = trim_unmatched_parens(name)
    # "peg 6,000" -> "peg 6000": the comma is a thousands separator, not a separator.
    name = re.sub(r"(?<=\d),(?=\d{3}\b)", "", name)
    # "polyethylene glycol (peg) 3350": a parenthetical abbreviation restating the name.
    name = re.sub(r"\s*\((?:peg|mme|mpeg|w/v|v/v)\)\s*", " ", name)
    # "2-methyl-2,4-pentanediol (mpd)": the same thing at the end of the name.
    name = re.sub(r"\s*\([a-z][a-z0-9-]{1,7}\)\s*$", "", name)
    name = _POLYMER_PREFIX.sub(r"\1 \2", name)
    # "peg 2000mme" and "peg2000mme" -> "peg mme 2000", matching how the MME reagents are
    # named in the lexicon. The suffix form is common enough to be worth normalising.
    name = re.sub(r"^(m?peg)\s*(\d{3,6})\s*-?\s*mme\b", r"\1 mme \2", name)
    return re.sub(r"\s+", " ", name).strip()


def strip_quantity(clause: str) -> str:
    """Remove a leading or trailing quantity, leaving the reagent name.

    Trailing digits inside the name survive on purpose: in "peg 3350" the number is the
    reagent's identity, not its amount.
    """
    stripped = _LEADING_QTY.sub("", clause, count=1)
    if stripped == clause:
        stripped = _TRAILING_QTY.sub("", clause, count=1)
    return tidy_name(stripped)


def classify(clause: str) -> str:
    """Bucket a clause as reagent, ph, temperature, method or protein_or_setup."""
    if not clause or not clause.strip():
        return "empty"
    if _PH_CLAUSE.search(clause):
        return "ph"
    if _TEMPERATURE.search(clause):
        return "temperature"
    if _REFERENCE.search(clause):
        return "reference_only"
    if _METHOD.search(clause):
        return "method"
    if _PROTEIN.search(clause):
        return "protein_or_setup"
    return "reagent"


def is_noise(name: str) -> bool:
    return bool(_NOISE.match(name)) or len(name) < 2
