"""Stage `assign.l1`: assign each condition to one of the seven Top96 precipitant classes.

Phase 1, level 1 of the three-level ontology (spec 6.2). The seven JCSG Top96 classes are
exactly the non-empty subsets of {Organic, PEG, Salt}, which is why there are seven and not
some rounder number:

    Organic · Organic/PEG · Organic/PEG/Salt · Organic/Salt · PEG · Salt · Salt/PEG

So L1 needs no curation: it follows from which of the three precipitant families are doing
work in a given condition. What it *does* need is two thresholds, and both are genuine
judgement calls rather than facts:

  PEG_ORGANIC_MAX_MW    Below this, a PEG behaves as an organic precipitant rather than a
                        polymer, so it counts towards Organic and not PEG. Spec 6.3 states
                        the principle ("PEG 400 and PEG 8000 are different reagents") without
                        fixing the boundary.
  SALT_PRECIPITANT_MIN   Below this, a salt is an additive setting ionic strength, not a
                        precipitant. 0.2 M NaCl beside 20% PEG is not a "Salt/PEG" condition;
                        1.5 M ammonium sulfate beside 20% PEG is.

Both are exposed as options, and `--sweep` reports how the class distribution moves with
them, because the right answer is a chemistry call informed by how much it actually changes.

Buffers, additives, detergents and explicitly declared cryoprotectants never contribute to
L1. Buffers collapse to the pH they set (spec 6.3), which is carried alongside the class
rather than folded into it.

    ./run.sh assign.l1
    ./run.sh assign.l1 --sweep
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Optional

import polars as pl

from .. import config
from ..manifest import Manifest

STAGE = "assign.l1"

# The seven classes, keyed by the frozenset of families present.
L1_CLASSES: dict[frozenset[str], str] = {
    frozenset({"organic"}): "Organic",
    frozenset({"peg"}): "PEG",
    frozenset({"salt"}): "Salt",
    frozenset({"organic", "peg"}): "Organic/PEG",
    frozenset({"organic", "salt"}): "Organic/Salt",
    frozenset({"peg", "salt"}): "Salt/PEG",
    frozenset({"organic", "peg", "salt"}): "Organic/PEG/Salt",
}

UNASSIGNED = "Unassigned"

DEFAULT_PEG_ORGANIC_MAX_MW = 600
# 0.2 M, on three independent lines of evidence rather than judgement:
#   1. Vendor formulations. Across the 92 screen wells holding both a polymer PEG and a salt,
#      salt molarity is median 0.20 with p25 = p75 = 0.20. PEG/Ion, the canonical PEG-plus-ion
#      family, formulates at 0.10 to 0.20 M.
#   2. A cliff in the corpus. Raising the threshold from 0.2 to 0.3 collapses Salt/PEG from
#      20.2% to 3.9%: depositors use exactly 0.2 M as the ion, and above 0.3 M is a different
#      regime of true salt precipitation.
#   3. The brief expects "PEG 3350 plus a salt" to swallow a large fraction of everything. At
#      0.5 M, Salt/PEG was a vestigial 2.5% and 92% of vendor PEG-plus-ion wells were
#      misclassified as plain PEG.
DEFAULT_SALT_PRECIPITANT_MIN_MOLAR = 0.2

# Percent thresholds below which a PEG or organic is not doing precipitant work either. A
# 2% PEG is an additive; the Top96 wells run from about 5% upwards.
MIN_PRECIPITANT_PERCENT = 4.0

_MOLAR_SCALE = {"molar": 1.0, "millimolar": 1e-3, "micromolar": 1e-6, "nanomolar": 1e-9}


def to_molar(concentration: Optional[float], unit: Optional[str]) -> Optional[float]:
    if concentration is None or unit not in _MOLAR_SCALE:
        return None
    return concentration * _MOLAR_SCALE[unit]


def family_of(component: dict[str, Any], peg_max_mw: int,
              salt_min_molar: float) -> Optional[str]:
    """Which precipitant family this component contributes to, if any.

    Returns None for buffers, additives, detergents, explicit cryoprotectants, unresolved
    reagents, and for anything present at a concentration too low to precipitate.
    """
    if component.get("cryo_evidence") == "explicit":
        return None
    chem_class = component.get("chem_class")
    if chem_class is None:
        return None

    concentration, unit = component.get("concentration"), component.get("unit")
    percent = concentration if (unit or "").startswith("percent") else None
    molar = to_molar(concentration, unit)

    if chem_class == "peg":
        if percent is not None and percent < MIN_PRECIPITANT_PERCENT:
            return None
        peg_mw = component.get("peg_mw")
        # A low molecular weight PEG is chemically an organic, not a polymer.
        if peg_mw is not None and peg_mw <= peg_max_mw:
            return "organic"
        return "peg"

    if chem_class in ("organic", "polyol"):
        if percent is not None and percent < MIN_PRECIPITANT_PERCENT:
            return None
        return "organic"

    if chem_class == "buffer":
        # Dual-role reagents: citrate, acetate, phosphate and tartrate are buffers at 0.1 M
        # and precipitating salts well above it. A Hofmeister rank in the lexicon is exactly
        # the marker that a buffer is also a salt, so it is used rather than a name list.
        if (component.get("hofmeister_rank") is not None
                and molar is not None and molar >= salt_min_molar):
            return "salt"
        return None

    if chem_class == "salt":
        # Only a salt above the threshold is precipitating; below it, it is setting ionic
        # strength and belongs with the additives.
        if molar is not None:
            return "salt" if molar >= salt_min_molar else None
        if percent is not None:
            return "salt" if percent >= MIN_PRECIPITANT_PERCENT else None
        return None

    if chem_class == "premix":
        # Tacsimate and the acid-salt mixes precipitate; the buffer premixes do not.
        return "salt" if component.get("premix_id") == "TACSIMATE" else None

    return None


def assign(components: pl.DataFrame, peg_max_mw: int,
           salt_min_molar: float) -> pl.DataFrame:
    families: dict[tuple, set[str]] = {}
    for row in components.iter_rows(named=True):
        key = (row["pdb_id"], row["crystal_id"])
        family = family_of(row, peg_max_mw, salt_min_molar)
        bucket = families.setdefault(key, set())
        if family:
            bucket.add(family)

    return pl.DataFrame(
        {
            "pdb_id": [k[0] for k in families],
            "crystal_id": [k[1] for k in families],
            "l1_precipitant_class": [
                L1_CLASSES.get(frozenset(v), UNASSIGNED) for v in families.values()
            ],
            "n_precipitant_families": [len(v) for v in families.values()],
        },
        schema={"pdb_id": pl.Utf8, "crystal_id": pl.Utf8,
                "l1_precipitant_class": pl.Utf8, "n_precipitant_families": pl.UInt8},
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--conditions", type=Path,
                        default=config.INTERIM_DIR / "parsed_conditions.parquet")
    parser.add_argument("--out", type=Path, default=config.INTERIM_DIR / "l1_classes.parquet")
    parser.add_argument("--peg-max-mw", type=int, default=DEFAULT_PEG_ORGANIC_MAX_MW)
    parser.add_argument("--salt-min-molar", type=float,
                        default=DEFAULT_SALT_PRECIPITANT_MIN_MOLAR)
    parser.add_argument("--sweep", action="store_true",
                        help="report how the distribution moves with the two thresholds")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    conditions = pl.read_parquet(args.conditions).filter(pl.col("discard_reason").is_null())
    keys = set(zip(conditions["pdb_id"], conditions["crystal_id"]))
    components = pl.read_parquet(args.components).filter(
        pl.struct(["pdb_id", "crystal_id"]).map_elements(
            lambda s: (s["pdb_id"], s["crystal_id"]) in keys, return_dtype=pl.Boolean))

    with Manifest(STAGE, params={"peg_max_mw": args.peg_max_mw,
                                 "salt_min_molar": args.salt_min_molar,
                                 "min_precipitant_percent": MIN_PRECIPITANT_PERCENT}) as m:
        m.add_input(args.components).add_input(args.conditions)

        if args.sweep:
            print("threshold sweep: share of usable records per class\n")
            header = f"  {'peg<=':>6} {'salt>=':>7}  " + "".join(
                f"{name[:12]:>13}" for name in
                ["Salt/PEG", "PEG", "Salt", "Organic/PEG", "Organic", "Unassigned"])
            print(header)
            for peg_mw in (400, 600, 1000):
                for salt_min in (0.2, 0.5, 1.0):
                    table = assign(components, peg_mw, salt_min)
                    counts = dict(table.group_by("l1_precipitant_class")
                                  .agg(pl.len().alias("n")).iter_rows())
                    total = table.height
                    row = f"  {peg_mw:>6} {salt_min:>7.1f}  "
                    for name in ["Salt/PEG", "PEG", "Salt", "Organic/PEG", "Organic",
                                 UNASSIGNED]:
                        row += f"{100 * counts.get(name, 0) / total:>12.1f}%"
                    print(row)
            print()

        table = assign(components, args.peg_max_mw, args.salt_min_molar)
        table.write_parquet(args.out, compression="zstd")

        distribution = (table.group_by("l1_precipitant_class").agg(pl.len().alias("n"))
                        .sort("n", descending=True))
        stats = {"n_records": table.height,
                 "n_unassigned": table.filter(
                     pl.col("l1_precipitant_class") == UNASSIGNED).height}
        for row in distribution.iter_rows(named=True):
            stats[f"n_{row['l1_precipitant_class'].replace('/', '_')}"] = row["n"]
        m.add_output(args.out).note(**stats)

        print(f"L1 assignment over {table.height:,} usable records "
              f"(peg<={args.peg_max_mw}, salt>={args.salt_min_molar} M)\n")
        for row in distribution.iter_rows(named=True):
            bar = "#" * int(60 * row["n"] / table.height)
            print(f"  {row['l1_precipitant_class']:<18} {row['n']:>7,} "
                  f"{100 * row['n'] / table.height:>5.1f}%  {bar}")
        print(f"\n  the seven classes are the non-empty subsets of "
              f"{{Organic, PEG, Salt}}; Unassigned means no component reached a "
              f"precipitating concentration")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
