"""Stage `assign.screen_match`: match parsed conditions against commercial screen wells.

WP5, and the two payoffs from spec 6.5:

1. **Orderable output.** "Maps to Crystal Screen 30, already in the fridge" beats
   "PEG 8000 at 30% with 0.2 M ammonium sulfate".
2. **Free parse validation.** An exact match to a published well is strong evidence the
   parse is right, and it costs no hand-labelling. The headline number this stage produces
   is: of the records whose component *set* matches a known well, what fraction also have
   every *concentration* within tolerance? That measures concentration and unit reading
   across tens of thousands of records.

It also separates screen hits from optimised conditions (spec 5.4). A record matching a
well's components but not its concentrations is very likely an optimised derivative of that
well, and is flagged `optimised_not_screen` rather than being counted as a hit.

**What this cannot validate.** Both sides are parsed with the same parser and the same
lexicon, so a systematic naming error (a wrong alias, a missing synonym) shifts both
identically and stays invisible here. Screen matching validates structure, concentration and
unit reading. Reagent naming is what the WP8 hand audit is for.

    ./run.sh assign.screen_match
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Optional

import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest
from ..parse.lexicon import load as load_lexicon
from ..parse.rules import RuleParser
from ..parse.schema import Component
from . import screens

STAGE = "assign.screen_match"

MATCH_SCHEMA = {
    "pdb_id": pl.Utf8, "crystal_id": pl.Utf8, "match_type": pl.Utf8,
    "screen": pl.Utf8, "catalogue": pl.Utf8, "well": pl.Utf8,
    "n_components": pl.UInt16, "n_concentration_agree": pl.UInt16,
    "ph_agrees": pl.Boolean, "well_condition_text": pl.Utf8,
}


def _component_from_row(row: dict[str, Any]) -> Component:
    return Component(
        role=row["role"], name_raw=row["name_raw"], name_canonical=row["name_canonical"],
        chem_class=row["chem_class"], concentration=row["concentration"],
        unit=row["unit"], cryo_evidence=row["cryo_evidence"],
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--conditions", type=Path,
                        default=config.INTERIM_DIR / "parsed_conditions.parquet")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "screen_matches.parquet")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    engine = RuleParser(load_lexicon())
    library = screens.load(engine)
    index = library.by_fingerprint()

    conditions = pl.read_parquet(args.conditions).filter(pl.col("discard_reason").is_null())
    keys = set(zip(conditions["pdb_id"], conditions["crystal_id"]))
    ph_by_key = dict(zip(zip(conditions["pdb_id"], conditions["crystal_id"]),
                         conditions["ph"]))

    components = pl.read_parquet(args.components)
    grouped = (components
               .filter(pl.struct(["pdb_id", "crystal_id"]).map_elements(
                   lambda s: (s["pdb_id"], s["crystal_id"]) in keys, return_dtype=pl.Boolean))
               .group_by(["pdb_id", "crystal_id"])
               .agg(pl.all()))

    with Manifest(STAGE, params={"n_screens": len({w.catalogue for w in library.wells}),
                                 "n_wells": len(library.wells),
                                 "n_wells_resolved": library.n_resolved}) as m:
        m.add_input(args.components).add_input(args.conditions)
        m.add_input(config.ONTOLOGY_DIR / "screens")

        rows: list[dict[str, Any]] = []
        for record in tqdm(grouped.iter_rows(named=True), total=grouped.height,
                           desc="records", unit="rec"):
            key = (record["pdb_id"], record["crystal_id"])
            parts = [
                _component_from_row({k: record[k][i] for k in
                                     ("role", "name_raw", "name_canonical", "chem_class",
                                      "concentration", "unit", "cryo_evidence")})
                for i in range(len(record["role"]))
            ]
            fingerprint = screens.fingerprint(parts)
            candidates = index.get(fingerprint)
            if not candidates:
                continue

            best: Optional[dict[str, Any]] = None
            for well in candidates:
                by_name = {c.name_canonical: c for c in well.components}
                agree = sum(
                    1 for c in parts
                    if c.name_canonical in by_name
                    and screens.concentrations_agree(c, by_name[c.name_canonical])
                )
                record_ph, well_ph = ph_by_key.get(key), well.ph
                ph_ok = (record_ph is not None and well_ph is not None
                         and abs(record_ph - well_ph) <= screens.PH_TOLERANCE)
                candidate = {
                    "pdb_id": key[0], "crystal_id": key[1],
                    "match_type": "exact" if agree == len(parts) and ph_ok else (
                        "components_and_concentrations" if agree == len(parts) else "components_only"),
                    "screen": well.screen, "catalogue": well.catalogue, "well": well.well,
                    "n_components": len(parts), "n_concentration_agree": agree,
                    "ph_agrees": ph_ok, "well_condition_text": well.condition_text[:300],
                }
                if best is None or candidate["n_concentration_agree"] > best["n_concentration_agree"]:
                    best = candidate
            if best:
                rows.append(best)

        matches = pl.DataFrame(rows, schema=MATCH_SCHEMA)
        matches.write_parquet(args.out, compression="zstd")

        n_records = conditions.height
        n_fingerprint = matches.height
        exact = matches.filter(pl.col("match_type") == "exact").height
        conc_ok = matches.filter(pl.col("n_concentration_agree") == pl.col("n_components")).height
        agreement = conc_ok / n_fingerprint if n_fingerprint else 0.0

        stats = {
            "n_kept_records": n_records,
            "n_component_set_matches": n_fingerprint,
            "component_set_match_rate": round(n_fingerprint / max(1, n_records), 4),
            "n_all_concentrations_agree": conc_ok,
            "concentration_agreement_rate": round(agreement, 4),
            "n_exact_including_ph": exact,
            "n_optimised_not_screen": n_fingerprint - conc_ok,
            "n_wells": len(library.wells),
            "n_wells_resolved": library.n_resolved,
        }
        m.add_output(args.out).note(**stats)

        print(f"\nscreen library: {len(library.wells)} wells across "
              f"{len({w.catalogue for w in library.wells})} screens, "
              f"{library.n_resolved} fully resolved ({library.n_resolved/len(library.wells):.1%})")
        print(f"\n{n_records:,} kept records")
        print(f"  component set matches a known well: {n_fingerprint:>7,} "
              f"({stats['component_set_match_rate']:.2%})")
        print(f"  ... and every concentration agrees: {conc_ok:>7,} "
              f"({agreement:.1%} of those)   <- free parse validation")
        print(f"  ... and the pH agrees too (exact):  {exact:>7,}")
        print(f"  flagged optimised_not_screen:       {n_fingerprint - conc_ok:>7,}")

        if n_fingerprint:
            print("\nmost-matched wells:")
            top = (matches.group_by(["screen", "well", "well_condition_text"])
                   .agg(pl.len().alias("n")).sort("n", descending=True).head(10))
            for row in top.iter_rows(named=True):
                print(f"  {row['n']:>5}  {row['screen']} {row['well']:>3}  "
                      f"{row['well_condition_text'][:64]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
