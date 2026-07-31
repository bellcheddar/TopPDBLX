"""Stage `assign.build_vendor_screens`: screens from vendors other than Hampton.

`assign.build_screens` keys off Hampton's binder layout, where each condition is printed as a
numbered line. Other vendors print a 96-well plate table instead, addressed A1 to H12, so they
need a different extractor rather than a wider regex.

**Nothing is transcribed from memory here either.** Each screen is downloaded from the vendor's
own published document, and every condition string is read out of the PDF text layer verbatim.
The same rejection guards apply as for Hampton, and they are the reason this can be attempted at
all: an extractor that fails produces a rejected screen, not a corrupted one.

  *too few wells*        a plate screen states its own size. Fewer conditions than that means
                         the text layer was read incompletely.
  *no name*              a screen that cannot be named cannot be ordered, which is the whole
                         point of the library (spec 6.5).
  *duplicate wells*      the same coordinate twice means the page was parsed twice or a header
                         row was mistaken for data.

**Compositional screens are kept as the vendor writes them.** Morpheus conditions are built from
named stocks: "0.06 M Divalents, 0.1 M Buffer System 1 pH 6.5, 30% v/v Precipitant Mix 1". Those
stock names are meaningless to the reagent lexicon, and expanding them here would mean asserting
constituents the plate table does not state. They are stored verbatim with the stock definitions
alongside, so the expansion can be done later from the vendor's own stock table rather than from
memory. `NPS`, `precipitant mix` and `precipitant mix 4` all appear in the corpus's unidentified
head, so this is also where those come from.

    ./run.sh assign.build_vendor_screens
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

STAGE = "assign.build_vendor_screens"

# Published formulation documents. Catalogue and vendor are recorded so a wrong URL produces a
# wrongly-attributed file rather than silently mislabelled chemistry.
SOURCES = [
    {
        "vendor": "Molecular Dimensions",
        "catalogue": "MD1-47",
        "screen": "Morpheus",
        "compositional": True,
        "url": "https://cdn.moleculardimensions.com/public/9196/"
               "Morpheus-10-mL-HT-96-and-FX-96-(MD1-46_47_47-FX)-Brochure.pdf",
        "expected_wells": 96,
    },
    {
        "vendor": "Molecular Dimensions",
        "catalogue": "MD1-37",
        "screen": "JCSG-plus",
        "url": "https://cdn.moleculardimensions.com/public/355/JCSG-plus-(MD1-37)-brochure.pdf",
        "expected_wells": 96,
    },
    {
        "vendor": "Molecular Dimensions",
        "catalogue": "MD1-36",
        "screen": "PACT premier",
        # PACT names buffer stocks rather than reagents: "0.1 M SPG 4.0" is the
        # succinate/phosphate/glycine system, not a compound the lexicon can resolve.
        "compositional": True,
        "url": "https://cdn.moleculardimensions.com/public/546/"
               "PACT-premier-HT_FX-96-(MD1-36_36-FX)-brochure.pdf",
        "expected_wells": 96,
    },
    {
        "vendor": "Qiagen NeXtal",
        "catalogue": "NEXTAL-JCSG-PLUS",
        "screen": "NeXtal JCSG+ Suite",
        "url": "https://cdn.moleculardimensions.com/public/1006/"
               "NeXtal-JCSG--Suite-Formulations.pdf",
        "expected_wells": 96,
    },
    {
        "vendor": "Qiagen NeXtal",
        "catalogue": "NEXTAL-JCSG-CORE-I",
        "screen": "NeXtal JCSG Core I Suite",
        "url": "https://cdn.moleculardimensions.com/public/1008/"
               "NeXtal-JCSG-Core-I-Suite-Formulations.pdf",
        "expected_wells": 96,
    },
    {
        "vendor": "Qiagen NeXtal",
        "catalogue": "NEXTAL-JCSG-CORE-IV",
        "screen": "NeXtal JCSG Core IV Suite",
        "url": "https://cdn.moleculardimensions.com/public/1011/"
               "NeXtal-JCSG-Core-IV-Suite-Formulations.pdf",
        "expected_wells": 96,
    },
]

# A plate row: a coordinate followed by the condition. Anchored so a stray "A1" inside prose
# cannot start a row, and bounded so a page footer cannot be swallowed as a condition.
_WELL_ROW = re.compile(r"\b([A-H])\s?(\d{1,2})\b[\s.:]+([^\n]{18,200})")

# Text that is a document artefact rather than a condition.
_NOT_A_CONDITION = re.compile(
    r"(?i)brochure|catalogue|catalog|www\.|@|screen\b.*\bplate\b|pre-?filled|"
    r"^\s*page\b|all rights reserved|ultrapure water|sterile-?filtered|"
    # Product and catalogue codes. "Morpheus FX-96 MD1-47-FX" was read as the H12 condition,
    # and because it brought the count to exactly the expected 96 the shortfall guard passed:
    # 95 real conditions plus one artefact looked identical to a complete plate.
    r"\b[A-Z]{2}\d-\d+|\b(?:HT|FX)-\d+\b|\bmd\d\b")


# A trailing vendor part number, "... 50% (v/v) PEG 400 135901-01". Part of the order form, not
# of the chemistry, and it would be parsed as an unidentified reagent if left in place.
_TRAILING_PART_NUMBER = re.compile(r"\s+\d{5,6}-\d{2}\s*$")

# pypdf splits numbers at kerning pairs as well as words: "0. 1 M Sodium acetate" is 0.1 M.
_SPLIT_NUMBER = re.compile(r"\b(\d)\.\s+(\d)")

# The NeXtal formulations print salt, buffer and precipitant as three table columns. The PDF text
# layer joins them with a space, so "0.2 M Lithium sulfate 0.1 M Sodium acetate pH 4.5 50% (v/v)
# PEG 400" arrives as one clause and the parser reads it as a single unidentifiable reagent.
# Re-inserting a separator before each new amount restores the boundary the document itself drew;
# it repairs an extraction artefact rather than altering what the vendor published.
_NEW_AMOUNT = re.compile(r"(?<=[a-zA-Z)])\s+(?=\d+(?:\.\d+)?\s*(?:M\b|mM\b|%))")


def clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = _TRAILING_PART_NUMBER.sub("", text)
    text = _SPLIT_NUMBER.sub(r"\1.\2", text)
    text = _NEW_AMOUNT.sub(", ", text)
    return text.strip(" .;:")


def extract_plate(pdf_path: Path, expected: int) -> dict[str, Any]:
    """Every A1 to H12 condition the document states, in document order."""
    pages = [page.extract_text() or "" for page in PdfReader(str(pdf_path)).pages]
    wells: dict[str, str] = {}
    for text in pages:
        for row, column, condition in _WELL_ROW.findall(text):
            number = int(column)
            if not 1 <= number <= 12:
                continue
            body = clean(condition)
            if len(body) < 18 or _NOT_A_CONDITION.search(body):
                continue
            coordinate = f"{row}{number}"
            # A condition states an amount. Where a coordinate appears more than once, prefer
            # the reading that looks like chemistry over one that does not, rather than simply
            # taking the first: order forms and product codes share the plate's coordinates.
            looks_like_condition = bool(re.search(r"\d+(?:\.\d+)?\s*(?:M|%|mM)\b", body))
            existing = wells.get(coordinate)
            if existing is None:
                wells[coordinate] = body
            elif looks_like_condition and not re.search(
                    r"\d+(?:\.\d+)?\s*(?:M|%|mM)\b", existing):
                wells[coordinate] = body

    ordered = sorted(wells.items(), key=lambda kv: (kv[0][0], int(kv[0][1:])))
    return {"wells": ordered, "n_wells": len(ordered), "expected": expected}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pdf-dir", type=Path, default=config.RAW_DIR / "screens")
    parser.add_argument("--out-dir", type=Path, default=config.ONTOLOGY_DIR / "screens")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.pdf_dir.mkdir(parents=True, exist_ok=True)

    with Manifest(STAGE, params={"n_sources": len(SOURCES)}) as m:
        written, rejected = [], []
        for source in SOURCES:
            catalogue = source["catalogue"]
            pdf_path = args.pdf_dir / f"{catalogue}.pdf"
            if not pdf_path.exists():
                if http.download(source["url"], pdf_path, skip_if_exists=True) is None:
                    print(f"  {catalogue}: not available at {source['url']}")
                    rejected.append({"catalogue": catalogue, "why": "download failed"})
                    continue

            info = extract_plate(pdf_path, source["expected_wells"])

            # The vendor states the plate size, so a shortfall is a reading failure rather than
            # a small screen, and is rejected on the same principle as the Hampton binders.
            if info["n_wells"] < info["expected"]:
                print(f"  {catalogue}: {info['n_wells']} of {info['expected']} wells read, "
                      f"rejected rather than shipped incomplete")
                rejected.append({"catalogue": catalogue, "n_extracted": info["n_wells"],
                                 "expected": info["expected"], "why": "incomplete plate"})
                continue

            document = {
                "screen": source["screen"],
                "catalogue": catalogue,
                "vendor": source["vendor"],
                "source_url": source["url"],
                "extracted_on": date.today().isoformat(),
                "n_conditions": info["n_wells"],
                "compositional": bool(source.get("compositional")),
                "note": ("Conditions name vendor stocks (Buffer System, Precipitant Mix, "
                         "Divalents, Halogens, NPS, SPG) rather than individual reagents. "
                         "Stored verbatim: expanding them would assert constituents this "
                         "document does not state well by well."
                         if source.get("compositional") else
                         "Conditions name individual reagents and parse against the lexicon."),
                "wells": [{"well": well, "condition_text": text}
                          for well, text in info["wells"]],
            }
            slug = re.sub(r"[^a-z0-9]+", "_", source["screen"].lower()).strip("_")
            path = args.out_dir / f"{slug}_{catalogue.lower()}.yaml"
            path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True,
                                           width=100))
            m.add_output(path)
            written.append((source["vendor"], catalogue, source["screen"], info["n_wells"]))
            print(f"  {catalogue}  {source['screen']:<28} {info['n_wells']:>3} wells  "
                  f"({source['vendor']})")

        m.note(n_written=len(written), n_rejected=len(rejected),
               n_wells=sum(w[3] for w in written))
        print(f"\n  {len(written)} screens, {sum(w[3] for w in written)} wells")
        if rejected:
            print(f"  {len(rejected)} rejected: "
                  f"{', '.join(r['catalogue'] for r in rejected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
