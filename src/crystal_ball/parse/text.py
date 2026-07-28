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

import regex as re

# Unicode dashes and lookalikes. Rare in this corpus (0 of 600 in the planning probe) but
# they break naive numeric regexes when they do appear.
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")

_NUMBER = r"\d+(?:[.,]\d+)?"
_RANGE = rf"{_NUMBER}(?:\s*(?:-|to|–)\s*{_NUMBER})?"
_UNIT_BODY = r"""(?:
      %\s*\(?\s*(?:w\s*/\s*v|v\s*/\s*v|w\s*/\s*w)\s*\)?
    | %
    | m\b | mm\b | µm\b | um\b | nm\b | mol/l\b
    | mg\s*/\s*ml\b | g\s*/\s*l\b | mg\s*/\s*l\b
)"""
_UNIT = _UNIT_BODY + "?"        # optional: "1.7 to 2.1 ammonium sulfate" omits the unit
_UNIT_REQUIRED = _UNIT_BODY

_SPLIT = re.compile(r"""
    \s*(?:
        [;\n]
      | ,(?!\d)          # a comma not followed by a digit, so "peg 6,000" stays whole
      | \s--+\s          # "bis-tris ph 7.0 -- 30% peg3350": a real separator in this corpus,
                         # spaced so it cannot eat the hyphen in "bis-tris" or "tris-hcl"
      | \s\+\s
      | \s+and\s+
      | \s+plus\s+
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
  | reservoir\s+solution | drop\s+(?:ratio|volume) | \bmicroliters?\b | \bul\b\s+of | \bµl\b
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


def normalise(text: str) -> str:
    """NFKC, unify dashes, collapse whitespace, lowercase."""
    text = unicodedata.normalize("NFKC", text).translate(_DASHES)
    return re.sub(r"\s+", " ", text).strip().lower()


def clauses(text: str) -> list[str]:
    return [c for c in _SPLIT.split(normalise(text)) if c and c.strip()]


def split_trailing_ph(clause: str) -> tuple[str, str | None]:
    """Separate a trailing pH from a reagent clause.

    "hepes ph 7.5" -> ("hepes", "7.5"). Without this, every buffer appears once per pH
    value in the candidate ranking and the tail looks far worse than it is.
    """
    match = _TRAILING_PH.search(clause)
    if not match:
        return clause, None
    return clause[:match.start()].strip(), match.group(1)


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


def tidy_name(name: str) -> str:
    """Light canonicalisation of a candidate reagent name."""
    name = name.strip().strip(" .:;,-")
    name = _unwrap(name)
    # "peg 3350)" and "(peg 3350": a stray unmatched paren left by clause splitting. Only
    # the unmatched one is removed, so balanced formulae like "(nh4)2so4" are untouched.
    while name.count(")") > name.count("(") and name.endswith(")"):
        name = name[:-1].strip()
    while name.count("(") > name.count(")") and name.startswith("("):
        name = name[1:].strip()
    # "peg 6,000" -> "peg 6000": the comma is a thousands separator, not a separator.
    name = re.sub(r"(?<=\d),(?=\d{3}\b)", "", name)
    # "polyethylene glycol (peg) 3350": a parenthetical abbreviation restating the name.
    name = re.sub(r"\s*\((?:peg|mme|mpeg|w/v|v/v)\)\s*", " ", name)
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
