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
wells. A disagreement is reported rather than resolved silently.

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
    "HR2-110", "HR2-112",            # Crystal Screen 1 and 2
    "HR2-126", "HR2-098",            # PEG/Ion
    "HR2-144",                       # Index
    "HR2-082", "HR2-084",            # PEGRx
    "HR2-107", "HR2-109",            # SaltRx
    "HR2-134", "HR2-136",            # further Hampton screens, identified from the PDF
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
        found = [(n, re.sub(r"\s+", " ", c).strip())
                 for n, c in _CONDITION.findall(text)]
        found = [(n, c) for n, c in found if len(c) >= MIN_CONDITION_LENGTH]
        if len(found) > len(best):
            best = found

    # Independent check: the column-wise table lists every tube number on its own line.
    declared = max((len(set(_TUBE_COUNT.findall(text))) for text in pages), default=0)

    return {
        "screen": name,
        "catalogue": catalogue,
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
            if not info["screen"]:
                info["screen"] = catalogue

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
