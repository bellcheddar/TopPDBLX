"""Stage `assign.build_screens`: transcribe commercial screen formulations from vendor PDFs.

WP4. The screen library has two payoffs (spec 6.5): it makes the output orderable ("maps to
PACT E6, already in the fridge" beats "PEG 3350 at 18.4%"), and every exact match to a known
well is strong evidence that a parse is correct, which yields a large validation set with no
hand-labelling.

**Nothing here is transcribed from memory.** Each screen is downloaded from the vendor's own
support materials, and every condition string is extracted verbatim from the PDF. The screen
name and catalogue number are read out of the document rather than assumed, so a wrong URL
produces a wrongly-named file rather than silently mislabelled chemistry.

Each vendor binder carries the formulation twice: once column-wise (salt / buffer /
precipitant) and once as complete one-line conditions on the scoring sheet. The scoring
sheet is used, and the column-wise table is counted as an independent check on the number of
wells. A disagreement is reported rather than identified silently.

    ./run.sh assign.build_screens
    ./run.sh assign.build_screens --catalogue HR2-110
"""

from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

import yaml
from pypdf import PdfReader

from .. import config, http
from ..manifest import Manifest

STAGE = "assign.build_screens"

BINDER_URL = "https://hamptonresearch.com/uploads/support_materials/{catalogue}_Binder.pdf"

# Catalogue numbers only. The screen name is read from the PDF, never assumed here.
CATALOGUES = [
    # Every catalogue number Hampton publishes a binder for, discovered by probing the
    # support-materials URL rather than by listing screens from memory. A number that
    # yields no binder, or a binder with too few wells to be a screen, is reported and
    # skipped: the alternative is a screen library containing invented wells, which
    # would corrupt the validation set more quietly than a missing screen ever could.
    "HR2-078", "HR2-079", "HR2-080", "HR2-081", "HR2-082", "HR2-084",
    "HR2-086", "HR2-095", "HR2-096", "HR2-098", "HR2-100", "HR2-101",
    "HR2-102", "HR2-103", "HR2-104", "HR2-105", "HR2-106", "HR2-107",
    "HR2-109", "HR2-110", "HR2-112", "HR2-114", "HR2-116", "HR2-117",
    "HR2-118", "HR2-120", "HR2-121", "HR2-122", "HR2-126", "HR2-128",
    "HR2-130", "HR2-131", "HR2-133", "HR2-134", "HR2-136", "HR2-137",
    "HR2-138", "HR2-139", "HR2-144", "HR2-211", "HR2-213", "HR2-214",
    "HR2-215", "HR2-217", "HR2-219", "HR2-221", "HR2-231", "HR2-233",
    "HR2-235", "HR2-237", "HR2-239", "HR2-240", "HR2-241", "HR2-243",
    "HR2-245", "HR2-247", "HR2-248", "HR2-249", "HR2-250",
    # Found by probing the full HR2-078 to HR2-259 range rather than by extending the list a
    # few numbers at a time. Three of these (145, 147, 150) publish under a lower-case
    # "_binder" suffix, so they were unreachable at any point in the probe until the download
    # learned to try both spellings.
    "HR2-145", "HR2-147", "HR2-150", "HR2-251", "HR2-252", "HR2-253",
    "HR2-254", "HR2-255", "HR2-256", "HR2-257",
]

_CONDITION = re.compile(r"^\s*(\d{1,3})\.\s{2,}(\S.*)$", re.M)
_SCREEN_NAME = re.compile(r"^(.*?)\s*(?:™|\(TM\))?\s*" + r"(HR\d-\d+)\s+Reagent Formulation",
                          re.M | re.I)

# The name does not always sit before the catalogue number on the same line. Two other layouts
# occur, and both cost a real screen:
#
#   Natrix \n TM HR2-116 Reagent Formulation     the trademark wraps, so the line-anchored match
#                                                captures "TM" and Natrix was rejected as
#                                                unnameable, while its 48 conditions read fine
#   HR2-136 Reagent FormulationSaltRx HT TM      the name trails the header with no separator
#
# Both are recovered rather than guessed: the name is still read out of the binder, from an
# adjacent position rather than the expected one.
_NAME_AFTER = re.compile(r"(HR\d-\d+)\s+Reagent Formulation\s*(.{3,60}?)\s*(?:™|\(TM\))",
                         re.I)
_DEGENERATE_NAME = re.compile(r"^(?:tm|\(tm\)|reagent|solutions.*|)$", re.I)
_TUBE_COUNT = re.compile(r"^\s*(\d{1,3})\.\s*$", re.M)

MIN_CONDITION_LENGTH = 12

# A real screen has at least this many wells. The HT (96-well plate) binders lay their
# formulations out differently and yield a handful of spurious lines instead; rejecting them
# here is deliberate, because a screen library with six invented wells in it would corrupt
# the validation set far more quietly than a missing screen would.
MIN_WELLS = 24

# A binder laid out as a 96-well plate prints its conditions with a plate coordinate, "(A1) 0.1 M
# Barium chloride". The line-numbered extractor reads only part of such a document (27 of 96 for
# the Additive Screen), so a partial screen would ship as though it were complete. Detected by the
# coordinate prefix rather than by well count, because a short screen is legitimate and a
# partially-read one is not.
_PLATE_COORDINATE = re.compile(r"^\(([A-H])(\d{1,2})\)\s")

# pypdf occasionally splits a word at a kerning pair: "T etraethylammonium bromide". Rejoined
# only when a single capital is followed by a space and a lower-case run, which no real reagent
# name looks like.
_SPLIT_WORD = re.compile(r"\b([A-Z]) ([a-z]{3,})")


# A condition too long for one printed line wraps onto an indented continuation, and matching
# only to end of line silently truncates it:
#
#   21.   0.1 M Sodium phosphate monobasic monohydrate, 0.1 M Potassium phosphate monobasic
#            0.1 M MES monohydrate pH 6.5, 2.0 M Sodium chloride
#
# Crystal Screen 2 condition 21 shipped as the two phosphates alone, with its buffer and its
# 2.0 M sodium chloride missing, which is a different condition rather than a shorter one. It
# was found by reconstructing Crystal Screen HT from its columns and noticing that the
# supposedly authoritative tube version was the shorter of the two.
#
# A continuation must be indented and must itself state an amount, so a footer, a footnote or a
# page header cannot be absorbed into the condition above it.
_CONTINUATION = re.compile(r"^\s+(\d+(?:\.\d+)?\s*(?:M\b|mM\b|%).*)$")


def _joined_condition_lines(text: str) -> list[tuple[str, str]]:
    """Numbered conditions with their wrapped continuation lines reattached."""
    conditions: list[tuple[str, str]] = []
    for line in text.split("\n"):
        start = _CONDITION.match(line)
        if start:
            conditions.append((start.group(1), start.group(2)))
            continue
        carry = _CONTINUATION.match(line)
        if carry and conditions:
            number, body = conditions[-1]
            separator = " " if body.rstrip().endswith(",") else ", "
            conditions[-1] = (number, f"{body.rstrip()}{separator}{carry.group(1)}")
    return conditions


def tidy_condition(text: str) -> str:
    """Repair artefacts introduced by the PDF text layer, never by the vendor."""
    return _SPLIT_WORD.sub(r"\1\2", text).strip()


# The HT binders print no tube numbers. They lay the screen out as three parallel columns, a
# whole Salt column, then a whole Buffer column, then a whole Precipitant column, with "None"
# where a condition has no component of that kind. A condition is recovered by position: the
# nth salt belongs with the nth buffer and the nth precipitant.
#
# Position is the only key available, which makes this the most dangerous extractor in the
# project. One dropped line shifts every condition after it and produces plausible chemistry
# that no vendor ever sold, with no tube number to catch it. Three things constrain it:
#
#   1. each column is truncated at its first non-chemistry line, so tube lists, footnotes and
#      the contact block cannot be read as reagents
#   2. the truncated columns must then be exactly equal in length, which is the check that a
#      dropped line would fail
#   3. the result is validated against a screen already extracted from numbered lines
#      (Crystal Screen HT against Crystal Screen), so the method is tested where the answer is
#      independently known before it is trusted where it is not
_COLUMN_HEAD = re.compile(r"(?m)^\s*(Salt|Buffer\s*◊?|Precipitant)\s*$")

# A component states an amount, or is the vendor's own "None". Anything else ends the column:
# "47. (D11)", "1. Available separately", "Website: hamptonresearch.com".
#
# The word boundary goes after M and mM only. "%" is itself a non-word character, so "30% v/v"
# has no boundary after the percent sign and a trailing \b rejected every precipitant line in
# the screen.
_COMPONENT_LINE = re.compile(r"^(?:None$|\d+(?:\.\d+)?\s*(?:M\b|mM\b|%))", re.I)


def extract_columns(pages: list[str]) -> list[tuple[str, str]]:
    """Recover conditions from the column-wise HT layout, or return nothing if it cannot."""
    conditions: list[tuple[str, str]] = []
    for text in pages:
        marks = [(m.end(), m.group(1).split()[0]) for m in _COLUMN_HEAD.finditer(text)]
        if not marks:
            continue
        bounds = [m.start() for m in _COLUMN_HEAD.finditer(text)] + [len(text)]
        tube_rows = len({int(n) for n in _TUBE_COUNT.findall(text)})

        column_order, columns = [], {}
        for index, (start, label) in enumerate(marks):
            body = [line.strip() for line in text[start:bounds[index + 1]].split("\n")
                    if line.strip()]
            kept: list[str] = []
            for line in body:
                if not _COMPONENT_LINE.match(line):
                    break
                # A cell holding two components wraps onto a second line, and the vendor's own
                # trailing comma is what marks the continuation: "0.8 M Sodium phosphate
                # monobasic monohydrate," then "0.8 M Potassium phosphate monobasic" is one
                # precipitant, not two. Four such cells are why Crystal Screen HT's precipitant
                # column held 52 lines against the salt column's 48.
                if kept and kept[-1].endswith(","):
                    kept[-1] = f"{kept[-1]} {line}"
                else:
                    kept.append(line)
            # The page states how many rows its table has, in the "Tube #" list down its left
            # edge, and that is the only thing that stops a column overrunning into whatever is
            # printed beneath it. JCSG+ prints its cryo formulations directly below the screen
            # table on the same page, so the salt column stopped at 48 by luck while buffer ran
            # to 50 and precipitant to 51, pulling in cryo conditions as though they were part
            # of the screen. Unequal columns then failed the length check and the whole screen
            # was lost, which is the right failure but for the wrong reason.
            if tube_rows:
                kept = kept[:tube_rows]
            if kept and label not in columns:
                column_order.append(label)
                columns[label] = kept

        # Unequal columns mean the text layer dropped a line, and a positional zip would then
        # pair the wrong reagents together. There is no way to tell which column lost it, so
        # the page is abandoned rather than repaired.
        # The precipitant column is mandatory, not merely one of three. Allowing a page through
        # on any two columns produced 42 Crystal Screen HT conditions that each looked entirely
        # plausible and had silently lost their precipitant: condition 1 came out as the calcium
        # chloride and acetate buffer with no mention of the 30% MPD that makes it a
        # crystallisation condition at all. A condition missing its precipitant is not a partial
        # reading, it is a different condition.
        # A two-column page is legitimate where the screen has no salt at all: Low Ionic
        # Strength prints only Buffer and Precipitant. What is never legitimate is losing the
        # precipitant, so that column is required rather than merely counted.
        sizes = {len(v) for v in columns.values()}
        if "Precipitant" not in columns or len(columns) < 2 or len(sizes) != 1:
            continue

        for row in range(sizes.pop()):
            parts = [columns[label][row] for label in column_order
                     if columns[label][row].lower() != "none"]
            if parts:
                conditions.append((str(len(conditions) + 1), tidy_condition(", ".join(parts))))
    return conditions


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def extract_screen(pdf_path: Path) -> dict[str, Any]:
    """Pull the screen name and the verbatim condition strings out of a vendor binder."""
    reader = PdfReader(str(pdf_path))
    pages = [(page.extract_text() or "") for page in reader.pages]

    name, catalogue = None, None
    for text in pages:
        match = _SCREEN_NAME.search(text)
        if not match:
            continue
        name = re.sub(r"\s+", " ", match.group(1)).strip(" ™-")
        catalogue = match.group(2).upper()

        if _DEGENERATE_NAME.match(name):
            # The line before the match, for the wrapped-trademark layout.
            head = text[:match.start()].rstrip().rsplit("\n", 1)
            previous = re.sub(r"\s+", " ", head[-1]).strip(" ™-") if head else ""
            after = _NAME_AFTER.search(text)
            if previous and not _DEGENERATE_NAME.match(previous):
                name = previous
            elif after:
                name = re.sub(r"\s+", " ", after.group(2)).strip(" ™-")
        break

    # The scoring-sheet page holds complete one-line conditions; the formulation page holds
    # the same data split across columns. Take the page with the most complete lines.
    per_page: list[list[tuple[str, str]]] = []
    for text in pages:
        found = [(n, tidy_condition(re.sub(r"\s+", " ", c)))
                 for n, c in _joined_condition_lines(text)]
        per_page.append([(n, c) for n, c in found if len(c) >= MIN_CONDITION_LENGTH])
    best = max(per_page, key=len, default=[])

    # A screen larger than one printed page continues on the next one, and taking only the best
    # page silently halves it. Index (HR2-144) prints 1 to 48 on one page and 49 to 96 on the
    # next, and shipped as a 48-condition screen for exactly this reason: every other Hampton
    # binder fits on a single page, so nothing else exposed it. Conditions the best page does
    # not carry are filled in from the others, keyed by the tube number the vendor printed.
    #
    # Gap-filling rather than merging is deliberate. The formulation page repeats the same
    # conditions in column form, and a merge that preferred one reading over another would let
    # a column fragment overwrite a complete line. A number already read is never replaced.
    seen = {n for n, _ in best}
    for page in per_page:
        if page is best:
            continue
        for number, condition in page:
            if number not in seen and not _PLATE_COORDINATE.match(condition):
                seen.add(number)
                best.append((number, condition))
    best.sort(key=lambda item: int(item[0]))

    # Independent check: the column-wise table lists every tube number on its own line.
    # Counted across the whole binder rather than per page. A screen printed 48 tubes to a page
    # declares 48 under a per-page maximum however many pages it runs to, which understated
    # every 96-condition screen: Index reported "tube column lists 48" against its own 96, and
    # PACT (HR2-147) was refused outright for disagreeing with a number that was wrong.
    declared = len({int(number) for text in pages for number in _TUBE_COUNT.findall(text)})

    # The HT binders print no numbered condition lines at all, so the line extractor returns a
    # handful of spurious matches and the screen is rejected. Fall back to the column layout.
    from_columns = False
    if len(best) < MIN_WELLS:
        recovered = extract_columns(pages)
        if len(recovered) > len(best):
            best, from_columns = recovered, True

    # A plate-coordinate prefix means this is a 96-well layout the line extractor only partly
    # reads, so what came out is a fragment of a screen rather than a small screen.
    plate_layout = sum(1 for _, c in best if _PLATE_COORDINATE.match(c))

    return {
        "screen": name,
        "catalogue": catalogue,
        "plate_layout_lines": plate_layout,
        "from_columns": from_columns,
        "n_conditions": len(best),
        "n_tubes_declared": declared,
        "wells": [{"well": n, "condition_text": c} for n, c in best],
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalogue", action="append", default=None,
                        help="restrict to one or more catalogue numbers")
    parser.add_argument("--pdf-dir", type=Path, default=config.RAW_DIR / "screens")
    parser.add_argument("--out-dir", type=Path, default=config.ONTOLOGY_DIR / "screens")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.pdf_dir.mkdir(parents=True, exist_ok=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    catalogues = args.catalogue or CATALOGUES

    with Manifest(STAGE, params={"catalogues": catalogues}) as m:
        written, total_wells = [], 0
        rejected: list[dict[str, Any]] = []
        mismatches: list[dict[str, Any]] = []

        for catalogue in catalogues:
            # Hampton spells the suffix both ways, "_Binder" and "_binder", and the server is
            # case-sensitive. Trying only one silently loses whole screens, JCSG+ HT among them.
            pdf_path = args.pdf_dir / f"{catalogue}.pdf"
            url = BINDER_URL.format(catalogue=catalogue)
            if not pdf_path.exists():
                for candidate in (url, url.replace("_Binder", "_binder")):
                    if http.download(candidate, pdf_path, skip_if_exists=True) is not None:
                        url = candidate
                        break
                else:
                    print(f"  {catalogue}: not available at {url}")
                    continue

            info = extract_screen(pdf_path)

            # A column-reconstructed screen has no tube number on each condition, so nothing in
            # the reconstruction itself can reveal a dropped line. The tube list printed
            # elsewhere in the same binder is the only independent witness, and it is required
            # rather than merely compared: Index HT reconstructed 48 conditions for a 96-well
            # product and looked perfectly well-formed doing it. Where the binder prints no tube
            # list at all there is no witness, and the screen is refused however plausible it
            # looks. That costs Crystal Screen HT, which reconstructs all 96 and matches Crystal
            # Screen 1 and 2 exactly; it is the same chemistry under a second catalogue number,
            # so the library loses nothing but a duplicate.
            if info["from_columns"] and len(info["wells"]) != info["n_tubes_declared"]:
                print(f"  {catalogue}: {len(info['wells'])} conditions rebuilt from the column "
                      f"layout but the binder declares {info['n_tubes_declared']} tubes, "
                      f"rejected for want of an independent check")
                rejected.append({"catalogue": catalogue, "n_extracted": len(info["wells"]),
                                 "expected": info["n_tubes_declared"],
                                 "why": "column layout with no matching tube count"})
                continue

            if len(info["wells"]) < MIN_WELLS:
                print(f"  {catalogue}: only {len(info['wells'])} conditions extracted "
                      f"(minimum {MIN_WELLS}), rejected rather than shipped incomplete")
                rejected.append({"catalogue": catalogue, "n_extracted": len(info["wells"])})
                continue
            if info["plate_layout_lines"]:
                print(f"  {catalogue}: 96-well plate layout, only "
                      f"{len(info['wells'])} of its conditions are readable by the line "
                      f"extractor, rejected rather than shipped as a short screen")
                rejected.append({"catalogue": catalogue, "n_extracted": len(info["wells"]),
                                 "why": "plate layout, partially read"})
                continue
            if not info["screen"] or len(info["screen"].strip(" \u2122")) < 3:
                # A screen whose name could not be read out of its own binder would ship as "TM"
                # or as its catalogue number. Better absent than unidentifiable: a user cannot
                # order from a screen they cannot name.
                print(f"  {catalogue}: screen name not readable from the binder "
                      f"({info['screen']!r}), rejected")
                rejected.append({"catalogue": catalogue, "n_extracted": len(info["wells"]),
                                 "why": "screen name unreadable"})
                continue

            # The two representations should describe the same number of wells. A shortfall
            # means the extraction missed lines, and a screen that ships with half its
            # conditions is worse than one that does not ship: Index shipped 48 of 96 for
            # months, and its corpus matches were counted against that half. Printing the
            # disagreement was not enough, because a warning among nineteen success lines is a
            # warning nobody reads. A short screen is now rejected on the same principle as an
            # unreadable one. An excess is still only noted: the tube column can list a control
            # or a blank that is not a condition.
            if info["n_tubes_declared"] and info["n_tubes_declared"] != info["n_conditions"]:
                mismatches.append({"catalogue": catalogue,
                                   "conditions": info["n_conditions"],
                                   "tubes_declared": info["n_tubes_declared"]})
                if info["n_conditions"] < info["n_tubes_declared"]:
                    print(f"  {catalogue}: {info['n_conditions']} conditions read but the tube "
                          f"column lists {info['n_tubes_declared']}, rejected rather than "
                          f"shipped as a partial screen")
                    rejected.append({"catalogue": catalogue,
                                     "n_extracted": info["n_conditions"],
                                     "expected": info["n_tubes_declared"],
                                     "why": "fewer conditions than tubes declared"})
                    continue

            document = {
                "screen": info["screen"],
                "catalogue": info["catalogue"] or catalogue,
                "vendor": "Hampton Research",
                "source_url": url,
                "retrieved": date.today().isoformat(),
                "n_wells": info["n_conditions"],
                "wells": info["wells"],
            }
            out = args.out_dir / f"{slugify(info['screen'])}_{catalogue.lower()}.yaml"
            out.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True,
                                          width=200))
            written.append(out)
            total_wells += info["n_conditions"]
            flag = "" if not info["n_tubes_declared"] or \
                info["n_tubes_declared"] == info["n_conditions"] else \
                f"  [tube column lists {info['n_tubes_declared']}]"
            print(f"  {catalogue}  {info['screen']:<34} {info['n_conditions']:>3} wells{flag}")
            m.add_output(out)

        m.note(n_screens=len(written), n_wells=total_wells,
               well_count_mismatches=mismatches, rejected=rejected)
        print(f"\n{len(written)} screens, {total_wells:,} wells written to {args.out_dir}")
        if mismatches:
            print("well-count disagreements between the two printed representations:")
            for bad in mismatches:
                print(f"  {bad['catalogue']}: {bad['conditions']} extracted, "
                      f"{bad['tubes_declared']} tube numbers listed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
