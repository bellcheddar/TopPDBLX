"""Which part of a deposition describes the condition, and which parts describe something else.

**A deposition is not always a condition.** Depositors routinely describe the protein sample
before the drop, and what they crystallised out of after it:

    PROTEIN SOLUTION: 10 MG/ML MMP8 IN 25 MM MES, 100 MM NACL, 20 MM CACL2, 0.1 MM ZNCL2,
    PH 6.0, WITH THREEFOLD EXCESS INHIBITOR IN DMF ADDED. RESERVOIR: 20% PEG 4000 ...

Every reagent before `RESERVOIR` is real, correctly spelled and correctly quantified, and the
crystal grew in none of them. The rule parser reads them as components of the condition, so
`MES`, `NACL`, `CACL2` and `ZNCL2` enter the dataset as chemistry that was never in the drop --
and `PH 6.0`, which is the storage buffer's, is attributed to the condition.

Measured against the shipped `parsed_components.parquet`: **2,385 records carry a protein section
before a condition marker, and 3,209 identified components occur only inside one.** The head of
that list is exactly a storage buffer and nothing like a screen -- sodium chloride 806, Tris 546,
DTT 399, sodium azide 195, TCEP 147. Sodium azide is the tell: it is a preservative that stops
things growing, so its presence in a crystallisation condition is close to a contradiction.
A further 1,566 records state a pH inside the protein section, and in 387 of them that is the
only pH in the text, so the condition's recorded pH is the storage buffer's.

**Why a span and not a keyword.** The reagents here are ordinary -- NaCl is NaCl -- so nothing
about the reagent identifies the problem, and a blocklist would delete real sodium chloride from
the 180,000 conditions that legitimately contain it. What marks them is *where in the sentence
they sit*, which is a property of the text and not of the chemistry.

**Deliberately low recall.** Both patterns must be present and in the right order: a protein
section that opens, and a condition section that follows it. A deposition that describes only a
protein buffer is left entirely alone, because with no condition text to contrast against there
is no evidence which reading is intended, and dropping every component would turn a record that
merely looks odd into one that says nothing. Roughly 13% of the corpus's sodium azide is caught
this way; the rest is not, and that is the intended trade. A missed protein buffer costs
precision on one record, while a false span deletes a real condition.
"""

from __future__ import annotations

import re

# Opens a passage about the protein sample rather than the drop. Each alternative needs a noun
# that can only mean the sample: "protein solution", "stock buffer", "protein at 12 mg/ml".
# `protein` alone is far too broad -- "PROTEIN WAS CRYSTALLIZED FROM 2M AMMONIUM SULFATE" is a
# perfectly ordinary way to state a condition, and 2,380 records in this corpus write it that way.
PROTEIN_SECTION = re.compile(
    r"(?:protein|enzyme|sample|complex)\s+(?:solution|stock|buffer)\b"
    r"|\b(?:protein|enzyme)\s+(?:was\s+)?(?:at|in)\s+~?[\d.]+\s*mg"
    r"|\b(?:stock|storage|purification|dialysis)\s+buffer\b"
    r"|\bdialy[sz]ed\s+(?:against|into)\b",
    re.IGNORECASE,
)

# Closes it: the text has moved on to what the crystal grew in.
CONDITION_SECTION = re.compile(
    r"\breservoir\b|\bmother\s+li\w+\b|\bwell\s+solution\b"
    r"|\bcrystalli[sz]ation\s+(?:solution|buffer|condition)\b"
    r"|\bprecipitant\s+solution\b|\bcrystalli[sz]ed\s+from\b"
    r"|\bequilibrat\w+\s+against\b",
    re.IGNORECASE,
)

# A grown crystal moved into something else. Unlike the protein section this has no closing
# marker in practice -- a soak is almost always the last thing a deposition describes -- so it
# runs to the end of the text unless a condition marker reopens it.
SOAK_SECTION = re.compile(
    r"\bsoaked?\s+(?:in|with|into)\b|\bsoaking\b"
    r"|\btransferr?ed\s+(?:to|into)\b"
    r"|\bcrystals?\s+were\s+(?:then\s+)?(?:incubated|immersed|placed)\b",
    re.IGNORECASE,
)


# The second half of a buffer system: "0.1 M CAPS/ Sodium hydroxide pH 10.5" names one buffer
# titrated with one base, not two reagents. Vendors write screen formulations this way as a
# matter of course, so it turns up in the deposition text, in the Rigaku screen extraction and in
# the head of the model's unrecognised-name list -- one pattern, three callers.
#
# Each of these is also a perfectly good reagent in its own right, which is what makes a
# blocklist wrong: `1.8 M ammonium sulfate, 22 mM acetic acid, 78 mM sodium acetate` states an
# acetate buffer as its two halves with real concentrations, and both belong in the dataset.
TITRANTS = frozenset({
    "SODIUM_HYDROXIDE", "POTASSIUM_HYDROXIDE", "HYDROCHLORIC_ACID",
    "ACETIC_ACID", "PHOSPHORIC_ACID", "NITRIC_ACID",
})


def is_buffer_titrant(text: str, offset: int, has_amount: bool) -> bool:
    """Is the clause at `offset` the titrant half of a `buffer/titrant` pair?

    Both tests are load-bearing. **The slash** is what distinguishes a titrant from a reagent:
    without it, `22 mM acetic acid` in a list is an ordinary component. **The absent amount** is
    what distinguishes a titrant from a genuine second component sharing a slash --
    `400 mM sodium phosphate monobasic / 1600 mM potassium phosphate dibasic` states two real
    reagents, and 8H28 writes both patterns in one deposition.

    Measured on the corpus: 32 of the 189 records that currently emit a titrant component match
    this, and the other 157 state a concentration and are left alone.
    """
    if has_amount:
        return False
    before = text[:offset].rstrip()
    return before.endswith("/")


# The same pair written without spaces around the slash. `100 mM HEPES / Sodium hydroxide` is
# split into two clauses and handled by `is_buffer_titrant`; `0.1 M CAPS/ Sodium hydroxide` is
# not split at all, so the whole clause fails to identify and CAPS is lost with the titrant.
# Two spellings of one pattern, needing two fixes.
_TITRANT_SUFFIX = re.compile(
    r"\s*/\s*(?:sodium\s+hydroxide|naoh|potassium\s+hydroxide|koh"
    r"|hydrochloric\s+acid|hcl|acetic\s+acid|phosphoric\s+acid|nitric\s+acid)\s*$",
    re.IGNORECASE,
)


def strip_titrant_suffix(name: str) -> str | None:
    """`caps/ sodium hydroxide` -> `caps`. None when there is no titrant suffix to strip.

    Anchored at the end so it cannot eat a real second reagent: `sodium phosphate monobasic /
    1600 mM potassium phosphate dibasic` has a quantity after the slash and never matches.
    """
    stripped = _TITRANT_SUFFIX.sub("", name)
    return stripped if stripped != name and stripped.strip() else None


def spans(text: str) -> list[tuple[int, int, str]]:
    """Half-open `(start, end, role)` ranges of `text` that describe something other than the drop.

    Returns `[]` for the overwhelming majority of depositions, which describe only a condition.
    """
    found: list[tuple[int, int, str]] = []

    protein = PROTEIN_SECTION.search(text)
    if protein:
        # Only when a condition section follows. Without one there is nothing to contrast
        # against, and the safe reading of an all-protein-buffer deposition is the literal one.
        condition = CONDITION_SECTION.search(text, protein.end())
        if condition:
            found.append((protein.start(), condition.start(), "protein_buffer"))

    soak = SOAK_SECTION.search(text)
    if soak:
        # A condition marker after the soak reopens growth chemistry: "soaked in 5 mM ligand in
        # the crystallisation solution containing 18% PEG" states the soak *and* the drop, and
        # swallowing the rest of the sentence would lose the PEG.
        reopen = CONDITION_SECTION.search(text, soak.end())
        end = reopen.start() if reopen else len(text)
        if end > soak.start():
            found.append((soak.start(), end, "soak"))

    return sorted(found)


def role_at(offset: int, ranges: list[tuple[int, int, str]]) -> str | None:
    """The scope role covering `offset`, or None when it is ordinary condition text."""
    for start, end, role in ranges:
        if start <= offset < end:
            return role
    return None


class Cursor:
    """Locates each clause in the original text, in order, so a clause can be given an offset.

    `clauses()` returns strings with no positions attached, and threading offsets through the
    splitter would touch every branch of it. Clauses arrive in document order, so a forward-only
    search from the last match recovers the position without that surgery -- and forward-only is
    what makes it safe when a clause repeats: "20% PEG, ... , 20% PEG" matches the second
    occurrence second, rather than pinning both to the first.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        # **Matched case-insensitively, and getting this wrong was silent and expensive.** The
        # splitter lowercases and tidies as it goes, so its clauses never match an upper-case
        # deposition -- and most of this corpus is upper case. Every `find` failed, every clause
        # fell back to the previous position, and on 2WWJ that put the *reservoir's* citrate
        # inside the protein span and threw away the condition's real pH of 5.5. Nothing raised;
        # the answer was merely wrong.
        self._haystack = text.lower()
        self._at = 0

    def locate(self, clause: str) -> int:
        """The offset of `clause`, advancing past it. Falls back to the running position.

        The fallback must never raise: it is called on every clause of every record, and tidying
        can leave a clause that is not a literal substring of the source at all. An unlocatable
        clause keeps the position of the one before it, which is the right neighbourhood even
        when the offset is not exact.
        """
        probe = clause.strip().lower()
        if probe:
            found = self._haystack.find(probe, self._at)
            if found < 0:
                # Tidying rewrote the interior -- collapsed spacing, stripped punctuation. The
                # opening words survive it far more often than the whole clause does, so degrade
                # to a prefix rather than straight to the stale position.
                words = probe.split()
                for take in (3, 2, 1):
                    if len(words) >= take:
                        found = self._haystack.find(" ".join(words[:take]), self._at)
                        if found >= 0:
                            break
            if found >= 0:
                self._at = found + len(probe)
                return found
        return self._at
