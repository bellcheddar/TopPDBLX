# Datasheet: toppdblx-conditions v0.1.0

Generated 2026-08-03 by `./run.sh release.datasheet`. Every figure is read
from the data at generation time, so this file cannot drift from what it describes.

**Schema version** `0.1.0-draft` · **Ontology version** `0.2.0`
· **Lexicon version** `0.8.2` (509 reagents,
1462 names)

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
3. **Reported conditions are often optimised, not screen hits.** Of the 79,676 records
   whose component set matches a published screen well, only 38,114 agree on every
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
| Usable records | 186,248 (93.5%) |
| Discarded, with a reason code | 12,937 |
| Components | 605,491 |
| Components identified as a canonical reagent | 518,373 (85.6%) |
| Records with a linked protein sequence | 195,985 |
| Distinct 30% identity sequence clusters | 23,868 |
| Classified into a precipitant class | 143,626 |
| Unclassified, with the reason recorded | 42,634 |
| Assigned at L2 (fallback) | 0 |

### Why records are discarded

Every X-ray record is accounted for: it either parsed or carries exactly one reason code. The
distribution is itself a result.

| Reason | Records | Share |
|--------|---------|-------|
| `NO_REAGENT_MATCH` | 5,649 | 2.84% |
| `TOO_SHORT` | 4,369 | 2.19% |
| `METHOD_ONLY` | 2,500 | 1.26% |
| `UNPARSEABLE_RESIDUAL` | 147 | 0.07% |
| `REFERENCE_ONLY` | 103 | 0.05% |
| `EMPTY` | 91 | 0.05% |
| `NON_CRYSTALLISATION_TEXT` | 78 | 0.04% |

### Identified components by chemical class

| Class | Components |
|-------|-----------|
| buffer | 164,659 |
| salt | 147,195 |
| peg | 127,439 |
| additive | 26,719 |
| organic | 23,366 |
| polyol | 20,319 |
| premix | 5,914 |
| detergent | 1,422 |
| other | 1,340 |

### How pH was attributed

`buffer` means the pH was stated against a buffer. `final` means the text explicitly said so.
`unstated` means a pH was found but the text does not say which it is: the parser records the
value and refuses to guess its meaning.

| Source | Records |
|--------|---------|
| buffer | 98,585 |
| unstated | 87,614 |
| final | 49 |

---

## 3. Provenance

- **Source.** RCSB Data GraphQL API, `exptl_crystal_grow` and `polymer_entities`, harvested
  over the entry id snapshot in `data/raw/entry_ids/`. Raw responses are retained gzipped.
- **Fidelity.** The API text was compared byte for byte against `_exptl_crystal_grow.pdbx_details` parsed from archive mmCIF with gemmi, **across the whole archive**: 205,943 of 205,943 comparable entries agreed on both the details string and the number of rows in the crystal-grow loop, an agreement rate of **100.0000%**. 205,949 entries were checked in total; 6 were present in the entry-id snapshot but absent from the archive snapshot taken a day later, consistent with withdrawal from the PDB in the interim, and are excluded from the rate rather than counted as failures.
- **Sequence linkage.** One record per entry, never one per chain. The condition is labelled
  with the largest polymer entity, and the largest *polypeptide* entity is recorded separately
  because clustering a protein against an RNA chain is meaningless.
- **Clustering.** MMseqs2 `easy-cluster`, 80% coverage, sensitivity 7.5 below 40% identity.
  Cluster ids at 30%, 50% and 90% ship with every record.
- **Screen matching.** 2910 wells across 9 Hampton screens, extracted verbatim from the
  vendor's own support-material PDFs, never transcribed from memory.

---

## 4. Known limitations of the method

- **Screen matching cannot validate reagent naming.** Both sides use the same parser and the
  same lexicon, so a systematic naming error moves both identically and stays invisible. It
  validates structure, concentration and unit reading only.
- **A component-set match is necessary but not sufficient.** A single-component condition such
  as `2.0 M ammonium sulfate` matches a screen well trivially, whether or not the depositor
  ever used that screen.
- **`curated_group` carries a precipitant class, and Unclassified is a real answer.** Every
  condition is sorted into one of the seven JCSG Top96 classes, or left Unclassified with the
  reason recorded: an unidentified reagent, no stated amount for a precipitant, no precipitant at
  all, or a premixed system that does not fit a seven-class taxonomy. Unclassified is the single
  largest outcome and should be filtered on rather than assumed away.
- **Cryoprotectant labels are mostly inferred.** Only about 2.2% of entries name a
  cryoprotectant explicitly. `cryo_evidence` distinguishes `explicit` from `inferred`, and the
  two must not be conflated: roughly four in five are inferences.

---

## 5. Files

| File | Contents |
|------|----------|
| `toppdblx-conditions-v0.1.0.jsonl.gz` | Canonical, one nested record per line |
| `toppdblx-conditions-v0.1.0.parquet` | Record level, sequence linkage flattened on |
| `toppdblx-components-v0.1.0.parquet` | One row per reagent |
| `toppdblx-components-v0.1.0.csv.gz` | The same, for spreadsheets |
| `toppdblx.duckdb` | Both tables plus `usable_conditions` and `condition_components` views |
| `schema-v0.1.0-draft.json` | JSON Schema, generated from the pydantic model |

---

## 6. Licence and citation

**Data:** Creative Commons Attribution 4.0 International (`CC-BY-4.0`). Share and adapt for any
purpose including commercially, with credit, a link to the licence, and an indication of
changes. Full terms in `LICENSE-DATA`.

**Code:** MIT (`LICENSE`).

**Cite as:** Deller, M. C. (2026). TopPDBLX: a parsed, normalised and sequence-linked
database of crystallisation conditions from the Protein Data Bank. Version
0.1.0.

**Cite the upstream sources too.** Attributing TopPDBLX does not discharge the obligation
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
