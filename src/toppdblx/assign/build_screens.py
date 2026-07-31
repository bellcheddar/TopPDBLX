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
]

_CONDITION = re.compile(r"^\s*(\d{1,3})\.\s{2,}(\S.*)$", re.M)
_SCREEN_NAME = re.compile(r"^(.*?)\s*(?:™|\(TM\))?\s*" + r"(HR\d-\d+)\s+Reagent Formulation",
                          re.M | re.I)
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


def tidy_condition(text: str) -> str:
    """Repair artefacts introduced by the PDF text layer, never by the vendor."""
    return _SPLIT_WORD.sub(r"\1\2", text).strip()


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def extract_screen(pdf_path: Path) -> dict[str, Any]:
    """Pull the screen name and the verbatim condition strings out of a vendor binder."""
    reader = PdfReader(str(pdf_path))
    pages = [(page.extract_text() or "") for page in reader.pages]

    name, catalogue = None, None
    for text in pages:
        match = _SCREEN_NAME.search(text)
        if match:
            name = re.sub(r"\s+", " ", match.group(1)).strip(" ™-")
            catalogue = match.group(2).upper()
            break

    # The scoring-sheet page holds complete one-line conditions; the formulation page holds
    # the same data split across columns. Take the page with the most complete lines.
    best: list[tuple[str, str]] = []
    for text in pages:
        found = [(n, tidy_condition(re.sub(r"\s+", " ", c)))
                 for n, c in _CONDITION.findall(text)]
        found = [(n, c) for n, c in found if len(c) >= MIN_CONDITION_LENGTH]
        if len(found) > len(best):
            best = found

    # Independent check: the column-wise table lists every tube number on its own line.
    declared = max((len(set(_TUBE_COUNT.findall(text))) for text in pages), default=0)

    # A plate-coordinate prefix means this is a 96-well layout the line extractor only partly
    # reads, so what came out is a fragment of a screen rather than a small screen.
    plate_layout = sum(1 for _, c in best if _PLATE_COORDINATE.match(c))

    return {
        "screen": name,
        "catalogue": catalogue,
        "plate_layout_lines": plate_layout,
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
            url = BINDER_URL.format(catalogue=catalogue)
            pdf_path = args.pdf_dir / f"{catalogue}.pdf"
            if http.download(url, pdf_path, skip_if_exists=True) is None:
                print(f"  {catalogue}: not available at {url}")
                continue

            info = extract_screen(pdf_path)
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

            # The two representations should describe the same number of wells. A gap means
            # the extraction missed lines, so it is surfaced rather than quietly accepted.
            if info["n_tubes_declared"] and info["n_tubes_declared"] != info["n_conditions"]:
                mismatches.append({"catalogue": catalogue,
                                   "conditions": info["n_conditions"],
                                   "tubes_declared": info["n_tubes_declared"]})

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
