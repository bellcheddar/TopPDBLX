# 🔮 TopPDBLX

> **Every crystallisation condition in the Protein Data Bank, parsed, normalised and linked to the sequence that produced it.**

![python](https://img.shields.io/badge/python-3.14-3776AB?logo=python&logoColor=white) ![records](https://img.shields.io/badge/records-186%2C162-467FF7) ![components](https://img.shields.io/badge/components-603%2C459-467FF7) ![parse coverage](https://img.shields.io/badge/parse%20coverage-93.5%25-00897B) ![archive fidelity](https://img.shields.io/badge/archive%20fidelity-100%25-00897B) ![tests](https://img.shields.io/badge/tests-388%20passing-00897B) ![data](https://img.shields.io/badge/data-CC--BY--4.0-9b51e0) ![code](https://img.shields.io/badge/code-MIT-9b51e0) ![phase 0](https://img.shields.io/badge/phase%200-complete-fcb900) ![phase 1](https://img.shields.io/badge/phase%201-complete-fcb900) ![author](https://img.shields.io/badge/author-Marc%20C.%20Deller%2C%20D.Phil.-1C244B)

<table>
<tr>
<td>🌐 <b>Website</b></td><td><a href="https://marcdeller.com" target="_blank" rel="noopener noreferrer">marcdeller.com</a></td>
<td>✉️ <b>Contact</b></td><td><a href="mailto:marc@marcdeller.com">marc@marcdeller.com</a></td>
<td>🐙 <b>GitHub</b></td><td><a href="https://github.com/bellcheddar/TopPDBLX" target="_blank" rel="noopener noreferrer">bellcheddar/TopPDBLX</a></td>
</tr>
</table>

---

Parse, normalise and curate every crystallisation condition in the Protein Data Bank, and link each one to the sequence of the construct that produced it. TopPDBLX turns the free-text `_exptl_crystal_grow.pdbx_details` field into typed components (reagent, concentration, unit, role), cross-references them against published commercial screen formulations, and attaches MMseqs2 cluster identifiers so that redundancy can be controlled properly.

**Why it matters:** the strongest lever on crystallisation success is construct design and the second strongest is choosing a screen that suits the protein, yet both are still done by intuition, because all the precedent sits in a quarter of a million unstructured strings that nobody can query. Turning that text into data is the precondition for every downstream question worth asking. It is useful for: crystallographers planning a screen, structural genomics groups mining historical outcomes, method developers who need a benchmark, and anyone building models over crystallisation space.

- Project brief: [`toppdblx_spec_v1.md`](toppdblx_spec_v1.md)
- Phase 0 implementation plan: [`PHASE0_PLAN.md`](PHASE0_PLAN.md)
- Dataset datasheet: [`DATASHEET.md`](DATASHEET.md)
- Roadmap to completion: [`ROADMAP.md`](ROADMAP.md)

---

## ✨ What it does

| Capability | Detail |
|---|---|
| **Parses the whole archive** | 199,185 crystallisation records from 198,691 PDB entries, keyed on `(pdb_id, crystal_id)` |
| **Typed components** | 603,459 reagents with role, concentration, unit, PEG molecular weight, Hofmeister rank and buffer pKa |
| **Separates text from chemistry** | Method notes, screen references and unnamed ligands are labelled `not_a_component`, so they never inflate a failure rate |
| **Accounts for every record** | Anything not parsed carries one of seven discard codes, with its raw text retained |
| **Links to sequence** | 184,229 usable records carry the construct sequence, UniProt accessions and cluster ids at 30%, 50% and 90% identity |
| **Cross-references screens** | 434 wells across 9 commercial screens, extracted verbatim from vendor support materials |
| **Refuses to guess** | A condition with an unrecognised reagent, no stated amount or a premixed system is Unclassified, with the reason recorded |
| **Records its own uncertainty** | Inferred units, inferred cryoprotectant roles and pH attribution are flagged, never presented as fact |
| **Reproducible by stage** | Every stage is one command and writes a manifest of input hashes, tool versions and git state |

## 🔄 Workflow

**In plain terms:** the Protein Data Bank holds about 200,000 crystallisation recipes, and every one was typed in free-hand by a different scientist. There is no agreed format. The same chemical appears as "PEG 3350", "peg3350" and "polyethylene glycol 3,350", amounts are written in a dozen notations, and some entries are whole paragraphs of narrative. The work is to turn that text into a table you can query, without inventing anything that is not there.

It runs as a chain of stages. Each is one command, each writes a manifest recording its inputs, tool versions and git state, and each can be re-run on its own.

```
   RCSB PDB archive
          │
          │  ingest.*          fetch every deposition over GraphQL, then byte-compare
          ▼                    the text against the archive mmCIF: 100.0000% agreement
   199,185 condition strings
          │
          │  parse.run_parser  split into clauses, read amounts and units, look each
          ▼                    name up in the reagent lexicon
   603,459 typed components  ─────────────┐
          │                               │ 15% the rules cannot read
          │  assign.classify              ▼
          ▼                        models.train_slm    a small language model, taught
   7 precipitant classes             (SmolLM2-360M)    by the rules' own correct output
   or Unclassified + reason               │
          │                               │ names it wants but the lexicon lacks
          │  link.*                       ▼
          ▼                        parse.curation_queue   grouped into ~10 decisions
   sequences + MMseqs2 clusters           │               for an expert to accept
          │                               │
          │  release.assemble             └──► ontology/synonyms.yaml ──┐
          ▼                                                            │
   JSONL · Parquet · CSV · DuckDB · JSON Schema · datasheet   ◄─────────┘
                                                        the lexicon feeds back
                                                        into the next parse
```

The two loops on the right are what makes it improve. The model learns from the text the rules already read correctly, so it costs nothing in hand-labelling; and the names it reaches for but cannot find become the next curation round, so an expert only ever sees decisions the machine could not make alone.

**The core of it is about ten lines.** Everything else is provenance, validation and the lexicon:

```python
for deposition in archive:
    components = []
    for clause in split_into_clauses(deposition.text):     # ";" "," newlines, " and ", ...
        amount, unit, name = read_quantity(clause)          # "20% (w/v) PEG 3350"
        reagent = lexicon.lookup(name)
        if reagent is None:                                 # retry, never destructive
            reagent = lexicon.lookup(strip_prose(clause))   # "crystal conditions were 0.1 M ..."
        components.append(reagent or Unresolved(name))

    families = {family_of(c) for c in components            # {Organic, PEG, Salt}
                if c.is_precipitant and c.amount is not None}

    if any(c.is_premix or c.is_unresolved for c in components) or not families:
        condition_class = Unclassified(reason)              # an answer, not a failure
    else:
        condition_class = SEVEN_CLASSES[families]
```

| Tool | What it does here |
|---|---|
| RCSB Data GraphQL API | Fetches every deposition, batched and resumable |
| gemmi | Reads the archive mmCIF for the byte-level fidelity gate |
| `regex` | Clause splitting, which turned out to be the hard part rather than the chemistry |
| `ontology/synonyms.yaml` | The reagent dictionary: 502 reagents, 1,265 spellings |
| pydantic | Enforces the schema and the chemical invariants on load |
| polars, pyarrow, duckdb | Tables, joins and the queryable release |
| MMseqs2 | Sequence clustering at 30%, 50% and 90% identity, to control redundancy |
| MLX-LM, SmolLM2-360M | Reads the residual the rules cannot, fine-tuned on Apple Silicon |
| Weights & Biases | Training runs, one named run per round |
| `condition_courtroom_v5.html` | Single-file browser tool for expert curation, no server |

**Why the fidelity gate comes first.** Everything downstream is a claim about what a depositor wrote, so the first stage proves the text was fetched intact: every condition string is byte-compared against the mmCIF in the 90 GB archive snapshot, including loop row counts. It agrees on 205,943 entries at 100.0000%. Without that, a parsing statistic would be measuring the API rather than the archive.

## 📊 Results

| Measure | Value |
|---|---|
| Records | 199,185 |
| Usable | **186,162 (93.5%)** |
| Components | 603,459, **85.2% resolved** to a canonical reagent (87.0% excluding text that names no chemistry) |
| Reagent lexicon | 502 reagents, 1,265 names (v0.3.0) |
| Linked sequences | 184,229 across **23,159** distinct 30% identity clusters |
| Screen-well matches | 45,547 component-set matches, 20,339 agreeing on every concentration |
| Archive fidelity | **100.0000%** over 205,943 entries against the 90 GB mmCIF snapshot |
| Condition classes | Seven JCSG Top96 precipitant classes (v0.3.0), **58.9% classified**, 41.1% honestly unclassified |
| Leak-free splits | 181,007 records, no cluster spanning folds at 30%, 50% or 90% |
| Tests | 388 passing |

The project brief anticipated a rule-based parser plateauing near 75%. It reaches 93.5%, because most of the difficulty in this corpus turned out to be clause splitting rather than chemistry: newlines, double spaces, `in`, spaced slashes and stray brackets all separate components, and handling them properly recovered far more than chasing reagent names did.

### Two denominators, both reported

12,295 of the "unresolved" components contain no chemistry at all: method notes (`streak seeded`), screen references (`hampton research index screen`), unnamed ligands (`protein`, `inhibitor`) and bare splitter fragments (`na`). No lexicon entry can ever match them, so counting them as reagents the parser failed to resolve measures an artefact rather than the parser. They carry the role `not_a_component` with an auditable reason, and both denominators are published: **85.2%** over every component, **87.0%** over those that actually name a substance.

### Why records are discarded

Every X-ray record is accounted for: it either parsed or carries exactly one reason code.

| Reason | Records | Share |
|---|---|---|
| `NO_REAGENT_MATCH` | 7,603 | 3.82% |
| `TOO_SHORT` | 4,369 | 2.19% |
| `METHOD_ONLY` | 2,400 | 1.21% |
| `UNPARSEABLE_RESIDUAL` | 301 | 0.15% |
| `REFERENCE_ONLY` | 103 | 0.05% |
| `EMPTY` | 91 | 0.05% |
| `NON_CRYSTALLISATION_TEXT` | 87 | 0.04% |

`REFERENCE_ONLY` at 103 records is worth noting: the brief expected "see publication" to be a major discard category, and it is negligible.

## 🗂️ Ontology

**In plain terms:** every crystallisation experiment is a mixture of a few ingredients. This sorts each one into a small set of buckets based on what kind of ingredient is doing the work, so you can ask "what usually crystallises proteins like mine?" and get an answer instead of a quarter of a million sentences.

There are seven buckets, plus an eighth for conditions that cannot be sorted honestly. That eighth is large, and that is the point: guessing would be worse than admitting the text does not say.

| Class | Conditions | Share |
|---|---|---|
| Unclassified | 76,479 | 41.1% |
| Salt/PEG | 45,710 | 24.6% |
| PEG | 21,445 | 11.5% |
| Salt | 19,398 | 10.4% |
| Organic/PEG | 7,053 | 3.8% |
| Organic/PEG/Salt | 7,049 | 3.8% |
| Organic/Salt | 6,309 | 3.4% |
| Organic | 2,719 | 1.5% |
| **Classified** | **109,683** | **58.9%** |

**For the crystallographer:** the seven classes are the JCSG Top96 precipitant classes, which are the non-empty subsets of {Organic, PEG, Salt}. Classification is by presence with no thresholds: a PEG is a PEG whatever its molecular weight and concentration, a salt is a salt whatever its chemistry and concentration. Buffers are excluded, since at 0.1 M a buffer sets the pH rather than precipitating anything (spec 6.3), and the pH is carried separately.

Unclassified is a first-class answer with its reason recorded, never a null:

| Reason it cannot be classified | Share |
|---|---|
| An unresolved reagent: the lexicon does not recognise a name, so nothing can be asserted | 22.0% |
| No amount stated for a precipitant: naming PEG without saying how much is not a measured condition | 13.1% |
| No precipitant at all | 3.4% |
| A premixed system (Morpheus, PACT, Tacsimate) that does not fit a seven-class taxonomy | 2.7% |

The premix decision settles spec 6.4, which had been open since Phase 0. Components stay expanded with their `premix_id`, so nothing is lost and the choice is reversible.

An earlier three-level ontology of 163 binned groups was withdrawn at v0.3.0. Its groups were derived by binning the corpus and then having labels retrofitted, which spec 6.1 rejects in its first line, and several were not chemically coherent: median purity across the 41 second-level groups was 49%. The full reasoning is in [`ontology/CHANGELOG.md`](ontology/CHANGELOG.md).

## ⚗️ Reagent classes

**In plain terms:** depositors write the same chemical a dozen different ways. "PEG 3350", "peg3350", "polyethylene glycol 3350" and "PEG 3,350" are one substance. The lexicon is the dictionary that maps every spelling onto one canonical name, and it is what makes the whole database queryable.

| Round | Reagents | Names | Component resolution |
|---|---|---|---|
| Seeded from corpus mining | 147 | 501 | 79.1% |
| Round 1: 40 frequency-ranked decisions | 165 | 570 | 80.1% |
| Round 2: 10 grouped decisions, 1,004 names | 502 | 1,265 | 84.5% |
| Prose stripping (a parser fix, not curation) | 502 | 1,265 | **85.2%** |

**For the crystallographer:** every reagent carries the chemistry the ontology needs, and each field is enforced on load rather than being optional documentation. A `peg` entry must state its molecular weight, a `buffer` must state its pKa, and a `premix` must list its constituents. Those invariants caught three separate attempts to bulk-add entries that could not satisfy them.

| Chemical class | Entries | What it carries |
|---|---|---|
| additive | 167 | ligands, cofactors and inhibitors carried into the drop |
| salt | 116 | Hofmeister rank, spanning sulfate (-4) to thiocyanate (+4) |
| peg | 91 | molecular weight, log-scaled; v/v below 600 and w/v above |
| buffer | 36 | pKa, since buffer identity collapses to the pH it sets |
| organic | 33 | alcohols and small organics reported v/v |
| polyol | 28 | glycerol, sugars and sugar alcohols |
| detergent | 16 | |
| premix | 9 | constituent ids for Tacsimate, Morpheus and similar |
| other | 6 | |

Two rounds of curation and one parser fix took resolution from 79.1% to 85.2%. The marginal return has since fallen to roughly 15 components per decision, so further rounds are no longer the best use of expert time.

## 🎓 Training

**In plain terms:** the rule-based parser reads most of the archive, but some depositions are written in prose it cannot follow. A small language model was trained to read those, by learning from the hundreds of thousands of examples the rule parser already handles correctly. It costs nothing in hand-labelling, because the rule parser writes its own teaching material.

| Round | What changed | Residual resolution | Schema validity |
|---|---|---|---|
| 01 | Bootstrap distillation, lexicon 0.1.0 | 87.0% | 98.8% |
| 02 | `not_a_component` class, confidence gate fixed | 89.7% | 98.9% |
| 03 | Cosine schedule, dropout, class rebalanced | 88.4% | 99.0% |
| 04 | Retrained on the 502-reagent lexicon | *in progress* | |

**For the crystallographer:** SmolLM2-360M under MLX-LM LoRA on an M1 Max, prompt masked so the loss falls on the answer rather than on echoing the question, evaluated only on the residual the rule parser could not read.

Three findings shaped how it is trained, and two of them contradict the obvious approach:

- **Validation loss is the wrong stopping signal.** The labels are the rule parser's own output, so validation loss measures fidelity to the teacher rather than skill on the residual. It flattens by iteration 400 while resolution is still climbing, and fidelity keeps improving to 91% long after resolution has plateaued. Checkpoints are chosen by sweeping residual resolution instead.
- **The useful training is far shorter than expected.** Resolution plateaus at about iteration 600, which is 0.09 of an epoch, confirmed on two independent curves with different batch sizes and schedules. A full epoch costs 10.7 hours and buys nothing measurable.
- **Gates are judged on the lower confidence bound**, so a lucky sample cannot pass them. An 800-record sweep once put two checkpoints on opposite sides of the resolution gate whose intervals overlapped entirely.

Every run is named `r1-parse-residual-smollm2-360m-roundNN` and logged to Weights & Biases, with the adapter directory carrying the same name so a checkpoint on disk traces back to the run that produced it.

## 🧪 Curation interfaces

Expert time is the scarcest input, so every audit is reduced to **tens of decisions ranked by corpus reach**, never a row-per-instance review.

| Round | Questions | Reach |
|---|---|---|
| Reagent lexicon, rounds 1 and 2 | 35, then 13 | Seeded the original lexicon |
| Lexicon gaps | 40 | 7,018 unresolved components |
| Lexicon gaps, grouped | 10 | 1,004 names, 34% of the unresolved mass |
| Classification accuracy | 200 | The per-class accuracy number (spec 6.6) |

`app/condition_courtroom_v5.html` renders any payload with the same shape, so a new audit is a new generator stage rather than a new page.

**A recommendation is an answer, not a suggestion.** Every dropdown is pre-set and there is an accept-all button, so a wrong recommendation is not caught, it is accepted in bulk. Generators therefore carry explicit chemistry guards: numbers must match exactly (a number in a reagent name is molecular weight or substitution position, never decoration), element and acid words must agree, and a family name such as bare `peg` resolves to nothing rather than inventing a member. Guards decide what is **recommended**, never what is **available**.

## 🔧 Installation

```bash
git clone https://github.com/bellcheddar/TopPDBLX.git
cd TopPDBLX
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e '.[dev]'
brew install mmseqs2          # required by the clustering stage
```

Large files live outside the repository. Point the data directories somewhere that is not inside an iCloud-synced folder, because macOS Optimize Mac Storage will silently evict them:

```bash
mkdir -p ~/TopPDBLXData/{raw,interim,processed}
ln -sfn ~/TopPDBLXData/raw       data/raw
ln -sfn ~/TopPDBLXData/interim   data/interim
ln -sfn ~/TopPDBLXData/processed data/processed
```

## 🚀 Usage

Every stage is a single command and writes its own manifest.

```bash
./run.sh --list                    # show all stages
./run.sh ingest.entry_ids          # snapshot the PDB entry id list
./run.sh ingest.fetch_entries      # batched GraphQL harvest (about 18 minutes)
./run.sh ingest.flatten            # raw JSON to parquet
./run.sh ingest.validate_fidelity  # gate: API text against archive mmCIF
./run.sh parse.run_parser          # free text to typed components
./run.sh assign.classify           # the seven JCSG Top96 precipitant classes
./run.sh assign.screen_match       # cross-reference commercial screens
./run.sh link.representative       # one sequence per entry
./run.sh link.cluster              # MMseqs2 at 30%, 50% and 90%
./run.sh release.assemble          # build the released database
./run.sh release.datasheet         # regenerate the datasheet
```

### Pipeline stages

| Stage | Purpose |
|---|---|
| `ingest.entry_ids` | Snapshots the entry id list: this defines the archive version for the release |
| `ingest.fetch_entries` | Batched GraphQL harvest, resumable, raw responses kept as provenance |
| `ingest.flatten` | One row per `(pdb_id, crystal_id)`, never index 0 of the crystal-grow loop |
| `ingest.validate_fidelity` | Byte-compares API text against archive mmCIF, including loop row counts |
| `ingest.targettrack` | Acquires and checksums the archived TargetTrack negative set for Phase 3 |
| `parse.mine_reagents` | Ranks candidate reagent names by corpus mass, to drive lexicon curation |
| `parse.lexicon_coverage` | Measures what share of the corpus the lexicon resolves |
| `parse.run_parser` | The deterministic rule parser, over the whole archive |
| `assign.build_screens` | Extracts screen formulations verbatim from vendor PDFs |
| `assign.screen_match` | Matches parsed conditions to screen wells, schema to schema |
| `link.representative` | Chooses one sequence per entry, never one per chain |
| `link.cluster` | MMseqs2 clustering at three identity thresholds |
| `link.sifts`, `link.uniprot` | PDB to UniProt mapping, and full-length reference sequences |
| `parse.lexicon_questions` | Ranks unresolved names by corpus mass into a few dozen curation calls |
| `parse.apply_lexicon_answers` | Folds audit answers back into the lexicon, with the reasoning recorded |
| `parse.curation_queue` | Groups the remaining gaps into ten bucketed decisions |
| `parse.apply_curation_queue` | Applies a bucketed answer to every name in its bucket |
| `assign.classify` | The seven JCSG Top96 precipitant classes, or Unclassified with a reason |
| `eval.class_audit` | Stratified accuracy audit, 25 conditions per class |
| `eval.splits` | Cluster-based train, validation and test splits, leak-free at all three thresholds |
| `eval.baselines` | Frequency prior and homology retrieval, the baselines any model must beat |
| `models.build_slm_dataset` | Bootstrap training data for the residual parser, no hand labelling |
| `models.train_slm` | LoRA fine-tune under MLX-LM, W&B logged, one named run per round |
| `models.eval_slm` | Fidelity and residual resolution, with Wilson intervals on both gates |
| `release.assemble` | Builds the released database in five formats |
| `release.snapshot` | Frozen 90 GB mmCIF archive, for the full-scale fidelity check |
| `release.verify_archive` | Compares the parsed source field against the archive, for every entry |

## 📦 Output

| File | Contents |
|---|---|
| `toppdblx-conditions-v0.1.0.jsonl.gz` | Canonical form, one nested record per line |
| `toppdblx-conditions-v0.1.0.parquet` | Record level, sequence linkage flattened on |
| `toppdblx-components-v0.1.0.parquet` | One row per reagent |
| `toppdblx-components-v0.1.0.csv.gz` | The same, for spreadsheets |
| `toppdblx.duckdb` | Both tables, plus `usable_conditions` and `condition_components` views |
| `schema-v0.1.0-draft.json` | JSON Schema, generated from the pydantic model |

```sql
-- which reagents crystallise the widest range of protein families,
-- without lysozyme and trypsin drowning the answer
SELECT name_canonical,
       count(DISTINCT cluster_30) AS families,
       count(*) AS uses
FROM condition_components
WHERE name_canonical IS NOT NULL
GROUP BY name_canonical
ORDER BY families DESC
LIMIT 10;
```

## 🧱 Stack

| Layer | Choice |
|---|---|
| Pipeline | Python 3.14, polars, pyarrow, duckdb, pydantic |
| Structure parsing | gemmi (mmCIF), used only for fidelity checking |
| Text | `regex`, for variable-width lookbehind and better unicode handling |
| Clustering | MMseqs2, shelled out |
| Interfaces | Single-file HTML and vanilla JavaScript, no build step |

## ⚠️ Known limitations

These determine what conclusions the data can support, and are stated in full in [`DATASHEET.md`](DATASHEET.md).

| Limitation | Consequence |
|---|---|
| The PDB contains only successes | Supports P(condition given crystallised), says nothing about P(crystallised given sequence) |
| Frequency reflects screen popularity | A common condition is evidence about what was tried, not about what works best |
| About 80% of percentage concentrations carry no w/v or v/v marker | Where a unit was inferred it is flagged `unit_inferred`: filter on it before treating a unit as reported fact |
| Only 2.2% of entries name a cryoprotectant explicitly | `cryo_evidence` separates `explicit` from `inferred`, and four in five are inferences |
| Depositor errors are reproduced, not corrected | A handful of records state nanomolar concentrations of bulk reagents |
| Screen matching cannot validate reagent naming | Both sides use the same lexicon, so a systematic naming error moves both identically |
| R1 labels come from the rule parser | Fidelity to `rules_v3` is a ceiling, not evidence of beating it: only residual resolution, where no label exists, measures a real gain |
| The residual parser cannot discover new chemistry | It emits only names it has seen, so a genuinely absent reagent looks the same as a model error. Closing that gap is curation, not modelling |

## ✅ To Do

- [x] Ingest the whole archive, with a byte-level fidelity gate
- [x] Deterministic rule parser with a stable discard taxonomy
- [x] Commercial screen library, extracted verbatim from vendor sources
- [x] Sequence linkage and MMseqs2 redundancy control
- [x] Assemble the release in five formats, with a generated datasheet
- [x] Settle licensing: CC-BY-4.0 data, MIT code
- [x] Complete the 90 GB archive snapshot and run the full-scale fidelity check
- [x] Reagent lexicon: two curation rounds, 147 to 502 reagents
- [x] Seven-class condition ontology, replacing the withdrawn three-level version
- [x] Fine-tune SmolLM2 on the parse residual, and strip narrative prose in the parser
- [ ] Classification accuracy audit: 200 judgements outstanding (spec 6.6)
- [ ] Apply the trained model over the residual, so it resolves components rather than sitting idle
- [ ] Supply pKa values for 59 buffers, or fix the clause splitter that produced them
- [ ] Publish to Zenodo for a citable DOI, and mirror on HuggingFace Datasets
- [ ] Browser front end (an exploration tool, not a predictor)
- [ ] Construct boundary model, then crystallisation propensity

## 📄 Licence

**Data:** Creative Commons Attribution 4.0 International, see [`LICENSE-DATA`](LICENSE-DATA).
**Code:** MIT, see [`LICENSE`](LICENSE).

Attributing TopPDBLX does not discharge the obligation to the sources it derives from: the Protein Data Bank (CC0), SIFTS and UniProt (both CC BY 4.0). Commercial screen formulations in `ontology/screens/` are transcriptions of vendor-published support materials, kept structurally separable so they can be withdrawn without breaking the release. Screen names are trademarks of their owners, used nominatively.

**Cite as:** Deller, M. C. (2026). TopPDBLX: a parsed, normalised and sequence-linked database of crystallisation conditions from the Protein Data Bank. Version 0.1.0.

---

## 👤 Author

**Marc C. Deller, D.Phil.**  
Structural biologist & drug discovery scientist  

<table>
<tr>
<td>🌐</td><td><a href="https://marcdeller.com" target="_blank" rel="noopener noreferrer">marcdeller.com</a></td>
<td>✉️</td><td><a href="mailto:marc@marcdeller.com">marc@marcdeller.com</a></td>
<td>🐙</td><td><a href="https://github.com/bellcheddar/TopPDBLX" target="_blank" rel="noopener noreferrer">github.com/bellcheddar/TopPDBLX</a></td>
</tr>
</table>
