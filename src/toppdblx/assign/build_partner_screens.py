"""Stage `assign.build_partner_screens`: screens from Rigaku/MiTeGen and Jena Bioscience.

A third printed layout, after Hampton's numbered binders (`assign.build_screens`) and the
96-well plate tables of Molecular Dimensions and Qiagen (`assign.build_vendor_screens`). Both
vendors here print one condition per line with its position at the front, so the extractor is
row-wise rather than column-wise, and each row carries its own identifier. That last point
matters: unlike the Hampton HT blocks, nothing has to be recovered by position, so a dropped
line costs one condition instead of corrupting every condition after it.

**Nothing is transcribed from memory.** Every condition is read out of the vendor's own
published technical sheet or brochure, verbatim.

Three vendors, three shapes:

  *Rigaku Wizard*    one screen per technical sheet, rows numbered by tube:
                     `1 20% (w/v) PEG 8000 100 mM CHES/ Sodium hydroxide pH 9.5`
  *Jena Bioscience*  many screens in one brochure, rows addressed by well, with the screen
                     name as a heading above each block:
                     `A1 25 % w/v PEG 1,500 100 mM SPG buffer; pH 4.0 none`
  *Mol. Dimensions*  rows addressed by box and well:
                     `1-1 0.1 M Tris 8.0 25 % v/v PEG 350 MME`

Rigaku Reagents no longer publishes formulations of its own: its catalogue, including the
Wizard line, now ships through MiTeGen, so the two are one source here rather than two.
Emerald Bio is the same story from further back, the Wizard screens having originated there,
which is why this module has no Emerald entry. Recording that is the point: an absent vendor
should be absent for a stated reason rather than by omission.

    ./run.sh assign.build_partner_screens
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

STAGE = "assign.build_partner_screens"

# Published formulation documents, with the vendor and catalogue recorded so a wrong URL yields
# a wrongly-attributed file rather than silently mislabelled chemistry.
WIZARD_SHEET = "https://www.mitegen.com/wp-content/uploads/2017/08/{slug}.pdf"

RIGAKU_SOURCES = [
    {"screen": "Wizard Classic 1", "catalogue": "1009530", "slug": "wiz1_rigaku_15"},
    {"screen": "Wizard Classic 2", "catalogue": "1009531", "slug": "wiz2_rigaku_15"},
    {"screen": "Wizard Classic 3", "catalogue": "1009532", "slug": "wiz3_rigaku_15"},
    {"screen": "Wizard Classic 4", "catalogue": "1009533", "slug": "wiz4_rigaku_15"},
    {"screen": "Wizard Precipitant Synergy", "catalogue": "1009535",
     "slug": "wps_rigaku2_2015"},
]

# Molecular Dimensions prints a box-and-well label, "1-1" for box 1 well 1. The two screens
# already in the library from this vendor (Morpheus, PACT premier) are both compositional, so
# until now no Molecular Dimensions screen contributed a single parseable reagent.
MD_SOURCES = [
    {"screen": "ProPlex", "catalogue": "MD1-38",
     "url": "https://cdn.moleculardimensions.com/public/578/ProPlex-(MD1-38)-brochure.pdf"},
    {"screen": "ProPlex Eco", "catalogue": "MD1-38-ECO",
     "url": "https://cdn.moleculardimensions.com/public/579/"
            "ProPlex-Eco-Screen-(MD1-38-ECO)-brochure.pdf"},
]

JENA_BROCHURE = ("https://www.jenabioscience.com/images/3a38c1d302/"
                 "JBS_brochure_-_Crystal_Screens.pdf")

# A Wizard row: the tube number, then a condition that must start with an amount. Requiring the
# amount is what keeps page numbers and footnote markers from being read as tube numbers.
_WIZARD_ROW = re.compile(r"^\s*(\d{1,3})\s+(\d+(?:[.,]\d+)?\s*(?:%|m?M\b).*)$", re.M)

# A Molecular Dimensions row is addressed by box and well, "1-1" being box 1 well 1.
_MD_ROW = re.compile(r"^\s*(\d{1,2}-\d{1,2})\s+(\d+(?:[.,]\d+)?\s*(?:%|m?M\b).*)$", re.M)

# A Jena row is addressed by well. Both plain (A1) and screen-prefixed (9/A1) forms occur.
_JENA_ROW = re.compile(r"^\s*(?:\d+/)?([A-H]\d{1,2})\s+(\d+(?:[.,]\d+)?\s*(?:%|m?M\b).*)$",
                       re.M)

# The heading above each block of Jena rows is the screen's name. Anchored to the vendor's own
# section names so a stray line of prose cannot be mistaken for a screen.
_JENA_HEADING = re.compile(
    r"^\s*((?:Classic|Basic|Membrane|Kinase|Nuc-Pro|PEG/Salt|Pentaerythritol|PACT\+\+|"
    r"JCSG\+\+|Wizard|Cryo|LCP)(?:\s+[\dIV]+)?)\s*$", re.M)

# Both vendors run components together with only a space between them, exactly as the Qiagen
# plate tables do. A separator goes in before each new amount, restoring the boundary the
# printed table drew with a column. The lookbehind allows a digit so that "PEG 8000 100 mM"
# splits after the molecular weight rather than inside it.
_NEW_AMOUNT = re.compile(r"(?<=[a-zA-Z)0-9])\s+(?=\d+(?:[.,]\d+)?\s*(?:%|m?M\b))")

# Jena writes an absent additive as the word "none". It is the vendor stating there is no
# fourth component, not a reagent, and it would otherwise parse as an unidentified one.
_NONE_TAIL = re.compile(r"[,;]?\s*\bnone\b\s*$", re.I)

# Page furniture that shares a line with a condition in the brochure's text layer.
_FURNITURE = re.compile(r"(?i)www\.|jenabioscience|mitegen|rigaku|building blocks|"
                        r"technical\s+sheet|page \d+")

MIN_WELLS = 24


def numbering_is_complete(labels: list[str]) -> bool:
    """Tube numbers run 1..N with no gaps, or the sheet was read incompletely.

    Every row states its own tube number, so unlike the column layouts this document carries
    its own witness and nothing external is needed. Wizard Precipitant Synergy is why the check
    exists: its sheet numbers rows to 192 because it is two 96-condition screens printed
    together, and 191 were read. Shipping that would have merged two products into one screen
    and lost a condition while doing it.
    """
    numbers = sorted(int(label) for label in labels)
    return numbers == list(range(1, len(numbers) + 1))


def plate_is_complete(labels: list[str]) -> bool:
    """Well addresses fill a rectangle, every row letter carrying the same columns.

    A plate table that reads 95 of 96 looks entirely healthy by count alone. JBScreen LCP came
    out one short, and a missing well is a missing condition however plausible the remainder.
    """
    rows: dict[str, set[int]] = {}
    for label in labels:
        rows.setdefault(label[0], set()).add(int(label[1:]))
    widths = {frozenset(columns) for columns in rows.values()}
    if len(widths) != 1:
        return False
    columns = widths.pop()
    return columns == set(range(1, len(columns) + 1))


def clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = _NEW_AMOUNT.sub(", ", text)
    # "none" marks an empty column, and an empty column is not always the last one: a row with
    # no buffer but an additive prints it in the middle, which left the previous component
    # reading as "PEG 3350 none". Stripped per component rather than only at the end of the row.
    parts = [_NONE_TAIL.sub("", part).strip(" ,;") for part in text.split(", ")]
    return ", ".join(part for part in parts if part and part.lower() != "none").strip(" ,.;:")


def extract_rows(pages: list[str], pattern: re.Pattern[str]) -> list[tuple[str, str]]:
    """Every numbered or well-addressed condition the document states, in document order."""
    rows: dict[str, str] = {}
    for text in pages:
        for label, condition in pattern.findall(text):
            body = clean(condition)
            if len(body) < 12 or _FURNITURE.search(body):
                continue
            # First reading wins. A later page repeating a label is the brochure's summary
            # table, which is abbreviated, not a correction of the formulation table.
            rows.setdefault(label, body)
    return list(rows.items())


def extract_jena_screens(pages: list[str]) -> dict[str, list[tuple[str, str]]]:
    """The brochure holds every Jena screen, so rows are grouped under the heading above them."""
    screens: dict[str, dict[str, str]] = {}
    # The heading carries across a page break. A screen too long for one page continues on the
    # next without repeating its name, and resetting per page silently dropped the continuation:
    # JCSG++ 1 to 4, Basic 2 and Membrane 3 were all rejected as too small for exactly that
    # reason, having lost every row printed after the first page.
    current: Optional[str] = None
    for text in pages:
        # Walk the page in order, remembering the most recent heading, so a row is attributed
        # to the screen it is printed under rather than to whichever heading appears first.
        for line in text.split("\n"):
            heading = _JENA_HEADING.match(line)
            if heading:
                current = re.sub(r"\s+", " ", heading.group(1)).strip()
                continue
            row = _JENA_ROW.match(line)
            if not row or current is None:
                continue
            body = clean(row.group(2))
            if len(body) < 12 or _FURNITURE.search(body):
                continue
            screens.setdefault(current, {}).setdefault(row.group(1), body)
    return {name: list(wells.items()) for name, wells in screens.items()}


def write_screen(out_dir: Path, vendor: str, screen: str, catalogue: str, url: str,
                 wells: list[tuple[str, str]]) -> Path:
    document = {
        "screen": screen,
        "catalogue": catalogue,
        "vendor": vendor,
        "source_url": url,
        "extracted_on": date.today().isoformat(),
        "n_conditions": len(wells),
        "compositional": False,
        "note": "Conditions name individual reagents and parse against the lexicon.",
        "wells": [{"well": well, "condition_text": text} for well, text in wells],
    }
    slug = re.sub(r"[^a-z0-9]+", "_", f"{vendor} {screen}".lower()).strip("_")
    path = out_dir / f"{slug}.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100))
    return path


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pdf-dir", type=Path, default=config.RAW_DIR / "screens")
    parser.add_argument("--out-dir", type=Path, default=config.ONTOLOGY_DIR / "screens")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.pdf_dir.mkdir(parents=True, exist_ok=True)

    with Manifest(STAGE, params={"n_rigaku": len(RIGAKU_SOURCES)}) as m:
        written: list[tuple[str, str, int]] = []
        rejected: list[dict[str, Any]] = []

        for source in RIGAKU_SOURCES:
            url = WIZARD_SHEET.format(slug=source["slug"])
            pdf_path = args.pdf_dir / f"MG-{source['slug']}.pdf"
            if not pdf_path.exists() and http.download(url, pdf_path,
                                                       skip_if_exists=True) is None:
                rejected.append({"screen": source["screen"], "why": "download failed"})
                continue
            pages = [(page.extract_text() or "") for page in PdfReader(str(pdf_path)).pages]
            wells = extract_rows(pages, _WIZARD_ROW)
            if len(wells) < MIN_WELLS or not numbering_is_complete([w for w, _ in wells]):
                highest = max((int(w) for w, _ in wells), default=0)
                print(f"  {source['screen']}: {len(wells)} conditions read, numbered to "
                      f"{highest}, rejected rather than shipped incomplete")
                rejected.append({"screen": source["screen"], "n_extracted": len(wells),
                                 "numbered_to": highest, "why": "incomplete numbering"})
                continue
            wells.sort(key=lambda item: int(item[0]))
            path = write_screen(args.out_dir, "Rigaku Reagents", source["screen"],
                                source["catalogue"], url, wells)
            m.add_output(path)
            written.append(("Rigaku Reagents", source["screen"], len(wells)))
            print(f"  {source['catalogue']:<10} {source['screen']:<30} {len(wells):>3} wells")

        for source in MD_SOURCES:
            pdf_path = args.pdf_dir / f"{source['catalogue']}.pdf"
            if not pdf_path.exists() and http.download(source["url"], pdf_path,
                                                       skip_if_exists=True) is None:
                rejected.append({"screen": source["screen"], "why": "download failed"})
                continue
            pages = [(page.extract_text() or "") for page in PdfReader(str(pdf_path)).pages]
            wells = extract_rows(pages, _MD_ROW)
            # Box-and-well labels carry their own completeness check: every box must hold the
            # same number of wells, and those wells must run from 1 with no gaps.
            boxes: dict[str, set[int]] = {}
            for label, _ in wells:
                box, well = label.split("-")
                boxes.setdefault(box, set()).add(int(well))
            shapes = {frozenset(v) for v in boxes.values()}
            complete = (len(shapes) == 1
                        and shapes.copy().pop() == set(range(1, len(shapes.copy().pop()) + 1)))
            if len(wells) < MIN_WELLS or not complete:
                print(f"  {source['screen']}: {len(wells)} wells do not fill complete boxes, "
                      f"rejected rather than shipped incomplete")
                rejected.append({"screen": source["screen"], "n_extracted": len(wells),
                                 "why": "incomplete boxes"})
                continue
            wells.sort(key=lambda kv: tuple(int(x) for x in kv[0].split("-")))
            path = write_screen(args.out_dir, "Molecular Dimensions", source["screen"],
                                source["catalogue"], source["url"], wells)
            m.add_output(path)
            written.append(("Molecular Dimensions", source["screen"], len(wells)))
            print(f"  {source['catalogue']:<10} {source['screen']:<30} {len(wells):>3} wells")

        jena_pdf = args.pdf_dir / "JBS-BROCHURE.pdf"
        if jena_pdf.exists() or http.download(JENA_BROCHURE, jena_pdf,
                                              skip_if_exists=True) is not None:
            pages = [(page.extract_text() or "") for page in PdfReader(str(jena_pdf)).pages]
            found = extract_jena_screens(pages)

            # A screen's heading carries across a page break, which is what makes a screen
            # printed over two pages readable at all. The cost is that a block whose heading is
            # not recognised is attributed to whichever screen was named last. JCSG++ 4 came out
            # at 96 wells where its siblings hold 24, having absorbed rows printed after its own
            # block ended. The brochure prints these screens in families of four of equal size,
            # so a member disagreeing with every sibling is the signature of that error, and a
            # family with only one surviving member has nothing to corroborate it either way.
            sizes: dict[str, list[int]] = {}
            for name, wells in found.items():
                sizes.setdefault(name.rsplit(" ", 1)[0] if name[-1].isdigit() else name,
                                 []).append(len(wells))

            for name, wells in sorted(found.items()):
                family = name.rsplit(" ", 1)[0] if name[-1].isdigit() else name
                if sum(1 for size in sizes[family] if size == len(wells)) < 2:
                    print(f"  JBScreen {name}: {len(wells)} wells, no sibling in the "
                          f"{family} family agrees, rejected as unverifiable attribution")
                    rejected.append({"screen": f"JBScreen {name}", "n_extracted": len(wells),
                                     "why": "no sibling agrees on size"})
                    continue
                if len(wells) < MIN_WELLS or not plate_is_complete([w for w, _ in wells]):
                    print(f"  JBScreen {name}: {len(wells)} wells do not fill a complete "
                          f"plate, rejected rather than shipped incomplete")
                    rejected.append({"screen": f"JBScreen {name}", "n_extracted": len(wells),
                                     "why": "incomplete plate"})
                    continue
                wells.sort(key=lambda item: (item[0][0], int(item[0][1:])))
                path = write_screen(args.out_dir, "Jena Bioscience", f"JBScreen {name}",
                                    f"JBS-{re.sub(r'[^A-Z0-9]+', '', name.upper())}",
                                    JENA_BROCHURE, wells)
                m.add_output(path)
                written.append(("Jena Bioscience", f"JBScreen {name}", len(wells)))
                print(f"  {'JBS':<10} {'JBScreen ' + name:<30} {len(wells):>3} wells")

        m.note(n_written=len(written), n_rejected=len(rejected),
               n_wells=sum(w[2] for w in written), rejected=rejected)
        print(f"\n  {len(written)} screens, {sum(w[2] for w in written)} wells")
        if rejected:
            print(f"  {len(rejected)} rejected: "
                  f"{', '.join(str(r.get('screen')) for r in rejected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
