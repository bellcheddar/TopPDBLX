# Datasheet: crystalball-conditions v0.1.0-dev

Generated 2026-07-29 by `./run.sh release.datasheet`. Every figure is read
from the data at generation time, so this file cannot drift from what it describes.

**Schema version** `0.1.0-draft` · **Ontology version** `0.0.0-unassigned`
· **Lexicon version** `0.1.0` (147 reagents,
501 names)

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
3. **Reported conditions are often optimised, not screen hits.** Of the 44,989 records
   whose component set matches a published screen well, only 20,004 agree on every
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
| Records (one per `pdb_id` + `crystal_id`) | 199,185 |
| Distinct PDB entries | 198,691 |
| Usable records | 182,973 (91.9%) |
| Discarded, with a reason code | 16,212 |
| Components | 594,756 |
| Components resolved to a canonical reagent | 472,885 (79.5%) |
| Records with a linked protein sequence | 195,985 |
| Distinct 30% identity sequence clusters | 23,868 |

### Why records are discarded

Every X-ray record is accounted for: it either parsed or carries exactly one reason code. The
distribution is itself a result.

| Reason | Records | Share |
|--------|---------|-------|
| `NO_REAGENT_MATCH` | 8,774 | 4.40% |
| `TOO_SHORT` | 4,369 | 2.19% |
| `METHOD_ONLY` | 2,236 | 1.12% |
| `UNPARSEABLE_RESIDUAL` | 531 | 0.27% |
| `REFERENCE_ONLY` | 107 | 0.05% |
| `NON_CRYSTALLISATION_TEXT` | 104 | 0.05% |
| `EMPTY` | 91 | 0.05% |

### Resolved components by chemical class

| Class | Components |
|-------|-----------|
| buffer | 154,785 |
| salt | 137,341 |
| peg | 120,239 |
| organic | 21,276 |
| polyol | 17,909 |
| additive | 15,706 |
| premix | 4,087 |
| other | 976 |
| detergent | 566 |

### How pH was attributed

`buffer` means the pH was stated against a buffer. `final` means the text explicitly said so.
`unstated` means a pH was found but the text does not say which it is: the parser records the
value and refuses to guess its meaning.

| Source | Records |
|--------|---------|
| buffer | 93,859 |
| unstated | 89,062 |
| final | 52 |

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
- **Screen matching.** 434 wells across 9 Hampton screens, extracted verbatim from the
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
| `crystalball-conditions-v0.1.0-dev.jsonl.gz` | Canonical, one nested record per line |
| `crystalball-conditions-v0.1.0-dev.parquet` | Record level, sequence linkage flattened on |
| `crystalball-components-v0.1.0-dev.parquet` | One row per reagent |
| `crystalball-components-v0.1.0-dev.csv.gz` | The same, for spreadsheets |
| `crystalball.duckdb` | Both tables plus `usable_conditions` and `condition_components` views |
| `schema-v0.1.0-draft.json` | JSON Schema, generated from the pydantic model |

---

## 6. Licence and citation

**To be completed before public release (spec decision 12.7).** The intended terms are
CC-BY-4.0 for the data and MIT for the code, with a Zenodo DOI and a HuggingFace mirror.

One caveat needs a decision rather than a default: `ontology/screens/` is a transcription of
published vendor formulations. Formulations are published facts and citing the vendor is
normal practice, but that directory is kept structurally separable so it can be withdrawn
without breaking the rest of the release.

Author: Marc C. Deller, D.Phil.
