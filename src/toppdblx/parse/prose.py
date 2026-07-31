"""Strip narrative prose that the clause splitter leaves attached to a reagent.

Depositors write sentences, not field values. The splitter divides on separators, so a clause can
arrive with a whole sentence wrapped around its chemistry:

    "crystal conditions were 100 mm bis-tris propane"
    "glycerol added as cryoprotectant"
    "then transferred to 100 mm tris-hcl"
    "protein buffer: 20 mm tris"

Each of those became one unidentifiable "reagent name". Measured across the corpus: **11,174
unidentified components (13.7%) are longer than 40 characters and 5,799 (7.1%) contain a verb or
other prose marker.** No amount of lexicon curation reaches them, because the string is not a
reagent name and never will be.

**Used only as a retry, never as a first pass.** `rules.parse_component` calls this when the
clause has already failed to identify a reagent, and keeps the stripped version only if it then identifies one.
So a parse that already works cannot be changed, and the worst case of a bad pattern here is
that a component stays unidentified, which it already was. That asymmetry is deliberate: the
alternative, stripping first and parsing second, would put every existing correct parse at risk
of a regex that trims too much.
"""

from __future__ import annotations

import regex as re
from typing import Optional

# Narrative that precedes the chemistry. Anchored to the start and required to end in a verb,
# preposition or colon, so it cannot eat into a reagent name: "sodium acetate" has no such
# boundary and is left alone.
_LEADING = re.compile(
    r"^(?:"
    r"(?:the\s+)?(?:crystal(?:lisation|lization|s)?|protein|reservoir|precipitant|mother|"
    r"cryo(?:protectant)?|final|initial|growth|drop|well|screen|soaking|storage)?\s*"
    r"(?:conditions?|solutions?|buffers?|liquors?|mixtures?|reagents?)?\s*"
    r"(?:was|were|is|are|contained|containing|consisted\s+of|composed\s+(?:of|by)|comprised|"
    r"used|using|made\s+(?:from|up\s+of)|prepared\s+(?:from|in|with)|grown\s+(?:from|in)|"
    r"transferred\s+to|added\s+to|dissolved\s+in|diluted\s+in|equilibrated\s+(?:against|with)|"
    r"mixed\s+with|over\s+a|against|:)"
    r"|then\b|subsequently\b|finally\b|briefly\b"
    r")\s*[:,]?\s+",
    re.I)

# Narrative that follows the chemistry. The reagent is what comes before it.
_TRAILING = re.compile(
    r"\s+(?:"
    r"(?:was|were|is|are)\s+(?:added|used|present|included|introduced)?|"
    r"added\s+(?:as|prior\s+to|before|after|to|for)\b.*|"
    r"used\s+(?:as|for)\b.*|"
    r"as\s+(?:a\s+)?(?:cryo(?:protectant)?|precipitant|additive|buffer)\b.*|"
    r"prior\s+to\b.*|before\s+(?:freezing|mounting|data)\b.*|"
    r"for\s+(?:cryo|freezing|data\s+collection)\b.*|"
    r"in\s+(?:the\s+)?(?:reservoir|drop|well)\b.*"
    r")$",
    re.I)

# A clause that is nothing but narrative once the chemistry is removed, or that never had any.
_NO_CHEMISTRY = re.compile(r"^(?:the\s+)?\w+(?:\s+\w+)?\s+(?:was|were|is|are)$", re.I)


def strip_prose(clause: str) -> Optional[str]:
    """The chemistry inside a narrative clause, or None when stripping changes nothing.

    Returns None rather than the original when there is nothing to strip, so the caller can tell
    a retry apart from a no-op without comparing strings.
    """
    text = (clause or "").strip()
    if not text:
        return None

    stripped = text
    # Repeated because narrative stacks: "the crystallisation solution was ... containing ...".
    # Bounded to keep a pathological string from looping.
    for _ in range(3):
        before = stripped
        stripped = _LEADING.sub("", stripped, count=1).strip()
        stripped = _TRAILING.sub("", stripped, count=1).strip()
        if stripped == before:
            break

    stripped = stripped.strip(" ,;:.")
    if not stripped or stripped == text:
        return None
    # Refuse to return something that is still obviously a sentence fragment: "the complex was"
    # reduces to "the complex", which is not chemistry and would only waste a lookup.
    if _NO_CHEMISTRY.match(stripped):
        return None
    return stripped
