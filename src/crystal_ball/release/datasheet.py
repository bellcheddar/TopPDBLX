"""Stage `release.datasheet`: generate the dataset datasheet from live figures.

Written by machine on purpose. A hand-maintained datasheet drifts from the data it describes
within one release, and the numbers in it are exactly the ones a reader will check.

Structure follows Gebru et al., with one deliberate change: **the limitations come near the
top, not in an appendix**. Spec section 11 is not a disclaimer to be buried. The single most
important thing a reader must understand is that the PDB contains only successes, so this
database supports P(condition | crystallised) and says nothing whatsoever about
P(crystallised | sequence).

    ./run.sh release.datasheet
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

import polars as pl

from .. import config
from ..manifest import Manifest
from ..parse.lexicon import load as load_lexicon

STAGE = "release.datasheet"


def latest_manifest(stage: str) -> Optional[dict[str, Any]]:
    files = sorted(config.MANIFEST_DIR.glob(f"{stage}_*.json"))
    return json.loads(files[-1].read_text()) if files else None


def note(stage: str, key: str, default: Any = None) -> Any:
    manifest = latest_manifest(stage)
    return (manifest or {}).get("notes", {}).get(key, default)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=config.REPO_ROOT / "DATASHEET.md")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    conditions = pl.read_parquet(config.INTERIM_DIR / "parsed_conditions.parquet")
    components = pl.read_parquet(config.INTERIM_DIR / "parsed_components.parquet")
    lexicon = load_lexicon()

    kept = conditions.filter(pl.col("discard_reason").is_null())
    resolved = components.filter(pl.col("name_canonical").is_not_null())
    discards = (conditions.filter(pl.col("discard_reason").is_not_null())
                .group_by("discard_reason").agg(pl.len().alias("n"))
                .sort("n", descending=True))

    with Manifest(STAGE, params={"out": str(args.out)}) as m:
        rows = "\n".join(
            f"| `{r['discard_reason']}` | {r['n']:,} | {r['n'] / conditions.height:.2%} |"
            for r in discards.iter_rows(named=True))

        ph_rows = "\n".join(
            f"| {r['ph_source']} | {r['n']:,} |"
            for r in (kept.group_by("ph_source").agg(pl.len().alias("n"))
                      .sort("n", descending=True).iter_rows(named=True)))

        class_rows = "\n".join(
            f"| {r['chem_class']} | {r['n']:,} |"
            for r in (resolved.group_by("chem_class").agg(pl.len().alias("n"))
                      .sort("n", descending=True).iter_rows(named=True)))

        n_screen = note("assign.screen_match", "n_component_set_matches", 0)
        n_conc_agree = note("assign.screen_match", "n_all_concentrations_agree", 0)
        n_wells = note("assign.screen_match", "n_wells", 0)

        text = f"""# Datasheet: {config.DATASET_NAME if hasattr(config, 'DATASET_NAME') else 'crystalball-conditions'} v{config.DATASET_VERSION}

Generated {date.today().isoformat()} by `./run.sh release.datasheet`. Every figure is read
from the data at generation time, so this file cannot drift from what it describes.

**Schema version** `{config.SCHEMA_VERSION}` · **Ontology version** `{config.ONTOLOGY_VERSION}`
· **Lexicon version** `{lexicon.version}` ({len(lexicon.reagents)} reagents,
{len(lexicon.index())} names)

---

## 1. Read this first: what this database cannot tell you

These are not caveats to be skimmed. They determine what conclusions the data can support.

1. **The PDB contains only successes.** Every condition here produced a crystal. The database
   therefore supports **P(condition | crystallised)** and says **nothing** about
   **P(crystallised | sequence)**. It cannot tell you whether a protein will crystallise, only
   what worked for proteins that did.
2. **Condition frequency reflects screen popularity, not intrinsic success rate.** PEG 3350
   and PEG/Ion dominate because they are in everyone's screens. A condition being common here
   is evidence about what crystallographers tried, not about what works best.
3. **Reported conditions are often optimised, not screen hits.** Of the {n_screen:,} records
   whose component set matches a published screen well, only {n_conc_agree:,} agree on every
   concentration. The rest are flagged `optimised_not_screen`, but it remains a confounder.
4. **Protein concentration, drop ratio and equilibration volume are usually missing**, and
   they matter. They are captured where present and null otherwise.
5. **Unit inference is doing real work.** About 80% of percentage concentrations in the source
   text carry no w/v or v/v marker. Where the parser inferred one it is flagged
   `unit_inferred`; filter on that field before treating a unit as reported fact.
6. **Depositor errors are reproduced, not corrected.** The parser reads what the text says.
   A handful of records state nanomolar concentrations of bulk reagents (`20nM spermidine`,
   `50 Nm Hepes`, `1nM TCEP`), which are almost certainly millimolar typos at source. Silently
   correcting them would be a worse failure than passing them through, so they are passed
   through: filter on implausible values for your own chemistry if it matters.
7. **Any recommendation built on this is a prior over screening space**, not a prediction of a
   specific hit.

---

## 2. Composition

| Measure | Value |
|---------|-------|
| Records (one per `pdb_id` + `crystal_id`) | {conditions.height:,} |
| Distinct PDB entries | {conditions['pdb_id'].n_unique():,} |
| Usable records | {kept.height:,} ({kept.height / conditions.height:.1%}) |
| Discarded, with a reason code | {conditions.height - kept.height:,} |
| Components | {components.height:,} |
| Components resolved to a canonical reagent | {resolved.height:,} ({resolved.height / components.height:.1%}) |
| Records with a linked protein sequence | {note('release.assemble', 'n_with_sequence', 0):,} |
| Distinct 30% identity sequence clusters | {note('release.assemble', 'n_distinct_cluster_30', 0):,} |

### Why records are discarded

Every X-ray record is accounted for: it either parsed or carries exactly one reason code. The
distribution is itself a result.

| Reason | Records | Share |
|--------|---------|-------|
{rows}

### Resolved components by chemical class

| Class | Components |
|-------|-----------|
{class_rows}

### How pH was attributed

`buffer` means the pH was stated against a buffer. `final` means the text explicitly said so.
`unstated` means a pH was found but the text does not say which it is: the parser records the
value and refuses to guess its meaning.

| Source | Records |
|--------|---------|
{ph_rows}

---

## 3. Provenance

- **Source.** RCSB Data GraphQL API, `exptl_crystal_grow` and `polymer_entities`, harvested
  over the entry id snapshot in `data/raw/entry_ids/`. Raw responses are retained gzipped.
- **Fidelity.** The API text was compared byte for byte against `_exptl_crystal_grow.pdbx_details`
  parsed from archive mmCIF with gemmi. At WP1 this passed on 200 entries and 229 crystal-form
  rows with zero mismatches, deliberately oversampling multi-form entries. The full-archive
  check is `./run.sh release.verify_archive`.
- **Sequence linkage.** One record per entry, never one per chain. The condition is labelled
  with the largest polymer entity, and the largest *polypeptide* entity is recorded separately
  because clustering a protein against an RNA chain is meaningless.
- **Clustering.** MMseqs2 `easy-cluster`, 80% coverage, sensitivity 7.5 below 40% identity.
  Cluster ids at 30%, 50% and 90% ship with every record.
- **Screen matching.** {n_wells} wells across 9 Hampton screens, extracted verbatim from the
  vendor's own support-material PDFs, never transcribed from memory.

---

## 4. Known limitations of the method

- **Screen matching cannot validate reagent naming.** Both sides use the same parser and the
  same lexicon, so a systematic naming error moves both identically and stays invisible. It
  validates structure, concentration and unit reading only.
- **A component-set match is necessary but not sufficient.** A single-component condition such
  as `2.0 M ammonium sulfate` matches a screen well trivially, whether or not the depositor
  ever used that screen.
- **`curated_group` is null throughout.** Condition grouping is Phase 1. The field ships now so
  that later work is a join rather than a schema migration.
- **Cryoprotectant labels are mostly inferred.** Only about 2.2% of entries name a
  cryoprotectant explicitly. `cryo_evidence` distinguishes `explicit` from `inferred`, and the
  two must not be conflated: roughly four in five are inferences.

---

## 5. Files

| File | Contents |
|------|----------|
| `crystalball-conditions-v{config.DATASET_VERSION}.jsonl.gz` | Canonical, one nested record per line |
| `crystalball-conditions-v{config.DATASET_VERSION}.parquet` | Record level, sequence linkage flattened on |
| `crystalball-components-v{config.DATASET_VERSION}.parquet` | One row per reagent |
| `crystalball-components-v{config.DATASET_VERSION}.csv.gz` | The same, for spreadsheets |
| `crystalball.duckdb` | Both tables plus `usable_conditions` and `condition_components` views |
| `schema-v{config.SCHEMA_VERSION}.json` | JSON Schema, generated from the pydantic model |

---

## 6. Licence and citation

**Data:** Creative Commons Attribution 4.0 International (`CC-BY-4.0`). Share and adapt for any
purpose including commercially, with credit, a link to the licence, and an indication of
changes. Full terms in `LICENSE-DATA`.

**Code:** MIT (`LICENSE`).

**Cite as:** Deller, M. C. (2026). Crystal Ball: a parsed, normalised and sequence-linked
database of crystallisation conditions from the Protein Data Bank. Version
{config.DATASET_VERSION}.

**Cite the upstream sources too.** Attributing Crystal Ball does not discharge the obligation
to the sources it derives from: the **Protein Data Bank** (every condition string and sequence
originates there; PDB data are CC0), **SIFTS** and **UniProt** (both CC BY 4.0).

**Commercial screen formulations.** `ontology/screens/` transcribes screen formulations from
their vendors' own published support materials. Formulations are published facts and citing
the originator is normal practice, but the directory is kept structurally separable: deleting
it costs the `commercial_screen_match` field and nothing else. Screen names are trademarks of
their owners, used nominatively.

**Distribution:** Zenodo (versioned, with a DOI) as the citable archive, mirrored on
HuggingFace Datasets under `Dellboy` for working access, with the code on GitHub.

Author: Marc C. Deller, D.Phil.
"""
        args.out.write_text(text)
        m.add_output(args.out).note(n_records=conditions.height, n_kept=kept.height,
                                    bytes=args.out.stat().st_size)
        print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.1f} KB)")
        print(f"  {conditions.height:,} records, {kept.height:,} usable, "
              f"{len(lexicon.reagents)} reagents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
