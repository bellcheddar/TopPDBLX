# 🔮 TopPDBLX

> **Every crystallisation condition in the Protein Data Bank, parsed, normalised and linked to the sequence that produced it.**

![python](https://img.shields.io/badge/python-3.14-3776AB?logo=python&logoColor=white) ![records](https://img.shields.io/badge/records-186%2C180-467FF7) ![components](https://img.shields.io/badge/components-603%2C459-467FF7) ![parse coverage](https://img.shields.io/badge/parse%20coverage-93.5%25-00897B) ![archive fidelity](https://img.shields.io/badge/archive%20fidelity-100%25-00897B) ![tests](https://img.shields.io/badge/tests-400%20passing-00897B) ![data](https://img.shields.io/badge/data-CC--BY--4.0-9b51e0) ![code](https://img.shields.io/badge/code-MIT-9b51e0) ![phase 0](https://img.shields.io/badge/phase%200-complete-fcb900) ![phase 1](https://img.shields.io/badge/phase%201-complete-fcb900) ![author](https://img.shields.io/badge/author-Marc%20C.%20Deller%2C%20D.Phil.-1C244B)

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
| **Typed components** | 610,076 reagents with role, concentration, unit, PEG molecular weight, Hofmeister rank and buffer pKa |
| **Separates text from chemistry** | Method notes, screen references and unnamed ligands are labelled `not_a_component`, so they never inflate a failure rate |
| **Accounts for every record** | Anything not parsed carries one of seven discard codes, with its raw text retained |
| **Links to sequence** | 184,229 usable records carry the construct sequence, UniProt accessions and cluster ids at 30%, 50% and 90% identity |
| **Cross-references screens** | 434 wells across 9 commercial screens, extracted verbatim from vendor support materials |
| **Refuses to guess** | A condition with an unrecognised reagent or no stated amount is Unclassified, with the reason recorded |
| **Records its own uncertainty** | Inferred units, inferred cryoprotectant roles and pH attribution are flagged, never presented as fact |
| **Reproducible by stage** | Every stage is one command and writes a manifest of input hashes, tool versions and git state |
| **Teaches a small model with a large one** | A 32B teacher labels the hard records; a 360M student learns from it and reads all 47,845. Recall 79.6% → 95.2% against hand-labelled truth |

## 📖 The words on this page

Crystallography and machine learning each bring their own vocabulary, and this page uses both.
Everything here is defined where it first matters, but this is the short version.

| Term | What it means here |
|---|---|
| **Crystallisation condition** | The recipe a protein was crystallised from: a few reagents, their concentrations, and a pH. This project parses about 200,000 of them |
| **Deposition text** | What the scientist actually typed into the PDB, free-hand, in no agreed format. The raw material |
| **Precipitant** | The reagent doing the work — it pulls the protein out of solution. Usually a PEG, a salt or an organic |
| **Drop / reservoir** | The two halves of a vapour-diffusion experiment. The drop holds protein plus condition; the reservoir pulls water out of it |
| **Mother liquor** | The solution a crystal grew in and sits in |
| **Cryoprotectant** | Added *after* growth to stop ice forming when the crystal is frozen. Real chemistry, but it did not crystallise anything |
| **Premix** | A vendor mixture sold as one bottle — Morpheus "Divalents", Tacsimate — that is really several reagents |
| **PEG** | Polyethylene glycol, the commonest precipitant. The number is its molecular weight, not an amount: "PEG 3350" is a size |
| **Hofmeister rank** | An ordering of salts by how strongly they push proteins out of solution |
| | |
| **Lexicon** | The dictionary mapping every spelling a depositor might use onto one **canonical id**. `peg3350`, `PEG 3,350` and `polyethylene glycol 3350` all become `PEG_3350` |
| **Canonical id** | The single name this project uses for a reagent, in `UPPER_SNAKE_CASE` |
| **The residual** | The records the rule parser could **not** read. This is what the language model is for, and it is never trained on them |
| **The gold set** | 96 records labelled by hand, twice over. The only ground truth here, used to measure and never to train |
| **LoRA** | A way of fine-tuning a model by training a small adapter rather than all its weights. Ours is 33 MB against a 360 M-parameter base |
| **Distillation** | Training a small model on a larger one's output. Here a 32-billion-parameter *teacher* labels text for a 360-million-parameter *student* |
| **Grounding** | Checking that a reagent the model named actually appears in the text it was given. Catches invention, which no accuracy score can |
| **Identification** | Whether an emitted name exists in the lexicon. A weaker test than grounding, and it has misled this project three times |

## 🔄 Workflow

**In plain terms:** the Protein Data Bank holds about 200,000 crystallisation recipes, and every one was typed in free-hand by a different scientist. There is no agreed format. The same chemical appears as "PEG 3350", "peg3350" and "polyethylene glycol 3,350", amounts are written in a dozen notations, and some entries are whole paragraphs of narrative. The work is to turn that text into a table you can query, without inventing anything that is not there.

It runs as a chain of stages. Each is one command, each writes a manifest recording its inputs, tool versions and git state, and each can be re-run on its own.

```
   RCSB PDB archive
          │
          │  ingest.*          fetch every deposition over GraphQL, then byte-compare
          ▼                    the text against the archive mmCIF: 100% agreement
   199,185 condition strings
          │
          │  parse.run_parser  split into clauses, read amounts and units, look each
          ▼                    name up in the reagent lexicon
   610,076 typed components ────────────────┐
          │  parse.scope     tells the drop │ 47,845 records the rules cannot read
          │  from the protein sample and a  │
          │  assign.classify                ▼
          ▼                          ┌─────────────────────────────────────────┐
   7 precipitant classes             │   THE TEACHING LOOP                     │
   or Unclassified + reason          │                                         │
          │                          │   Qwen2.5-32B  ──labels──►  SmolLM2-360M│
          │  link.*                  │   models.          the      models.     │
          ▼                          │   teacher_label    student  train_slm   │
   sequences + MMseqs2 clusters      │        ▲                        │       │
          │                          │        │                        │       │
          │                          │        └──── gold_metrics ◄─────┘       │
          │                          │              scores both against        │
          │                          │              96 hand-labelled records   │
          │                          └────────────────┬────────────────────────┘
          │                                           │ names it wants but
          │                                           │ the lexicon lacks
          │                                           ▼
          │                                    gold_bench · courtroom
          │                                    an expert, tens of decisions
          │                                           │
          │  release.assemble                         ▼
          ▼                              ontology/synonyms.yaml ──┐
   JSONL · Parquet · CSV · DuckDB · JSON Schema · datasheet ◄─────┘
                                                    the lexicon feeds back
                                                    into the next parse
```

### The teaching loop, and why there are two models

**Why bother with two models: the teacher is 90x larger and 400x slower.** At 40 s/record a 32B would need 22 days to read the 47,845 residual records the rules cannot; the 360M student does it in under two hours. So the teacher reads ~2,000 of them to produce training labels, and the student does the work — which lifted recall on hand-labelled truth from **79.6% to 95.2%**, worth 46 reagents the rules never find, at a cost of 0.1 points of precision.

**In plain terms:** a small model is cheap to run over 200,000 records but not clever enough to teach itself. A large model is clever enough to teach but far too slow to run over the whole archive. So the large one reads a few thousand of the hard cases, the small one learns from what it produces, and the small one does the actual work. The expert never labels a corpus: they label 96 records that decide whether any of it worked.

The cycle has three participants and each does the one thing it is best at:

| | Does | Costs | Why it cannot do the others' job |
|---|---|---|---|
| **Expert** (`gold_bench`, `courtroom`) | Labels 96 records; answers tens of curation questions | An hour | Cannot label 47,845 records, and should never be asked to |
| **Teacher** (Qwen2.5-32B, local) | Labels a few thousand residual records | ~40 s/record | Far too slow for the corpus, and 91% precise where the student is 99.6% |
| **Student** (SmolLM2-360M) | Reads all 47,845 residual records | ~0.1 s/record | Bootstrapped from the rules, so the rules are its ceiling until a teacher lifts it |

**Why a teacher is needed at all.** The student was taught entirely by the rule parser, on records the rules read *confidently*. It therefore never sees an example the rules got wrong, and cannot learn to beat them. Round 06 made that visible: sweeping its checkpoints, fidelity to `rules_v3` climbed to 93.6% while identification on the residual peaked at 2,000 iterations and then fell. Read at the time as *training longer buys a better imitation and a worse reader* — and the direction of that reading did not survive contact with labelled truth, which prefers the 6,000-iteration adapter. What the divergence shows is that the two metrics measure different things, not which checkpoint to keep.

**Why this teacher, when it scores worse than its student.** On the 96 gold records the 32B reaches 91.0% precision and 89.1% recall, against the 360M's 99.6% and 91.5%. It loses. But their recalls are statistically indistinguishable (p = 0.33) and **their errors are different errors**: the teacher finds 18 correct reagents that the rules and the student together miss, and taking neither away drops the misses from 25 to 7 out of 294. It is not a better reader, it is a *differently wrong* one, and that is precisely what makes it useful as a source of labels.

**Why the expert sits at the top of the loop and not inside it.** Nothing else in the chain can tell a model that is silently missing reagents from one that is doing well: every automated measure here is precision-shaped and blind to what was never mentioned. The 96 labelled records are the only thing that can, so they are the yardstick and are never trained on, and every teacher run excludes them by name.

The other loop is unchanged and still pays: names the parser reaches for but cannot find are grouped into a few dozen curation calls, so the expert only ever sees decisions the machine could not make alone, and the lexicon they produce feeds back into the next parse.

**The core of it is about ten lines.** Everything else is provenance, validation and the lexicon:

```python
for deposition in archive:
    components = []
    for clause in split_into_clauses(deposition.text):     # ";" "," newlines, " and ", ...
        amount, unit, name = read_quantity(clause)          # "20% (w/v) PEG 3350"
        reagent = lexicon.lookup(name)
        if reagent is None:                                 # retry, never destructive
            reagent = lexicon.lookup(strip_prose(clause))   # "crystal conditions were 0.1 M ..."
        components.append(reagent or Unidentified(name))

    families = {family_of(c) for c in components            # {Organic, PEG, Salt}
                if c.is_precipitant and c.amount is not None}

    if any(c.is_unidentified for c in components) or not families:
        condition_class = Unclassified(reason)              # an answer, not a failure
    else:
        condition_class = SEVEN_CLASSES[families]
```

| Tool | What it does here |
|---|---|
| RCSB Data GraphQL API | Fetches every deposition, batched and resumable |
| gemmi | Reads the archive mmCIF for the byte-level fidelity gate |
| `regex` | Clause splitting, which turned out to be the hard part rather than the chemistry |
| `ontology/synonyms.yaml` | The reagent dictionary: 499 reagents, 1,583 spellings |
| pydantic | Enforces the schema and the chemical invariants on load |
| polars, pyarrow, duckdb | Tables, joins and the queryable release |
| MMseqs2 | Sequence clustering at 30%, 50% and 90% identity, to control redundancy |
| MLX-LM, SmolLM2-360M | The student: reads the residual the rules cannot, fine-tuned on Apple Silicon |
| MLX, Qwen2.5-32B-Instruct-4bit | The teacher: labels the hard records locally, so the corpus never leaves the machine |
| Weights & Biases | Training runs, one named run per round |
| `condition_courtroom_v7.html` | Single-file curation and accuracy audit, one condition per screen, no server |
| `gold_bench_v1.html` | Single-file labeller for the 96 gold records that judge every model round |

**Why the fidelity gate comes first.** Everything downstream is a claim about what a depositor wrote, so the first stage proves the text was fetched intact: every condition string is byte-compared against the mmCIF in the 90 GB archive snapshot, including loop row counts. It agrees on all 205,943 entries, at 100%. Without that, a parsing statistic would be measuring the API rather than the archive.

## 📊 Results

| Measure | Value |
|---|---|
| Records | 199,185 |
| Usable | **188,039 (94.4%)** |
| Components | 610,076, **86.5% identified** as a canonical reagent (89.0% excluding text that names no chemistry) |
| Reagent lexicon | 499 reagents, 1,583 names (v0.9.2) |
| Linked sequences | 184,229 across **23,159** distinct 30% identity clusters |
| Screen-well matches | 81,802 component-set matches, 39,219 agreeing on every concentration |
| Archive fidelity | **100%** over 205,943 entries against the 90 GB mmCIF snapshot |
| Condition classes | Seven JCSG Top96 precipitant classes (v0.3.1), **80.2% classified**, 19.8% honestly unclassified |
| Parser accuracy | Against hand-labelled records, both batches pooled: **92.5% precision, 92.0% recall**, F1 **92.3** (rules alone: 95.1% / 69.5%) |
| Leak-free splits | 181,007 records, no cluster spanning folds at 30%, 50% or 90% |
| Tests | 492 passing |

The project brief anticipated a rule-based parser plateauing near 75%. It reaches 93.5%, because most of the difficulty in this corpus turned out to be clause splitting rather than chemistry: newlines, double spaces, `in`, spaced slashes and stray brackets all separate components, and handling them properly recovered far more than chasing reagent names did.

### Two denominators, both reported

16,971 of the "unidentified" components contain no chemistry at all: method notes (`streak seeded`), screen references (`hampton research index screen`), unnamed ligands (`protein`, `inhibitor`) and bare splitter fragments (`na`). No lexicon entry can ever match them, so counting them as reagents the parser failed to identify measures an artefact rather than the parser. They carry the role `not_a_component` with an auditable reason, and both denominators are published: **86.5%** over every component, **89.0%** over those that actually name a substance.

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
| Salt/PEG | 59,601 | 31.7% |
| Unclassified | 37,208 | 19.8% |
| PEG | 28,623 | 15.2% |
| Salt | 27,922 | 14.8% |
| Organic/PEG/Salt | 11,220 | 6.0% |
| Organic/PEG | 9,629 | 5.1% |
| Organic/Salt | 9,498 | 5.1% |
| Organic | 4,338 | 2.3% |
| **Classified** | **150,831** | **80.2%** |

The rules alone reach 59.5%. The rest is the model reading records no regular expression could,
plus premixes now classified on what they are made of rather than refused.

**For the crystallographer:** the seven classes are the JCSG Top96 precipitant classes, which are the non-empty subsets of {Organic, PEG, Salt}. Classification is by presence with no thresholds: a PEG is a PEG whatever its molecular weight and concentration, a salt is a salt whatever its chemistry and concentration. Buffers are excluded, since at 0.1 M a buffer sets the pH rather than precipitating anything (spec 6.3), and the pH is carried separately.

Unclassified is a first-class answer with its reason recorded, never a null:

| Reason it cannot be classified | Share |
|---|---|
| No amount stated for a precipitant: naming PEG without saying how much is not a measured condition | 14.2% |
| No precipitant at all | 5.4% |
| An unidentified reagent: the lexicon does not recognise a name, so nothing can be asserted | 0.4% |

**The second row is a deliberate choice and the expensive one.** Those 24,327 conditions name their precipitant unambiguously and simply never state a concentration: `PEG6000, Sodium Chloride, VAPOR DIFFUSION`. Classifying them as `Salt/PEG` would lift classified coverage from 58.9% to roughly 72% in one line, and the rule that a PEG is a PEG whatever its concentration would arguably support it. They stay Unclassified because a condition with no concentration is not a measured condition, and coverage is not worth buying with a claim the deposition does not make.

**Premixes are no longer refused, and that is a reversal.** Spec 6.4 was settled by declaring every premixed system Unclassified, which was right while a premix was an opaque token. Once the vendor compositions were transcribed (lexicon v0.9.0) it stopped being right: 59% of the conditions it refused were blocked by a premix made only of *buffers* — MES/imidazole, phosphate-citrate, MIB, SPG — and a buffer is excluded from naming the class anyway. Those were ordinary conditions declined for their packaging rather than their chemistry. A premix now contributes the chemistry it is made of, so Morpheus Precipitant Mix 4 is MPD with PEG 1000 and PEG 3350, which is `Organic/PEG`. Classified coverage moved 76.8% to **80.2%**. A premix with no transcribed composition is still Unclassified, because there is nothing to expand it into.

An earlier three-level ontology of 163 binned groups was withdrawn at v0.3.0. Its groups were derived by binning the corpus and then having labels retrofitted, which spec 6.1 rejects in its first line, and several were not chemically coherent: median purity across the 41 second-level groups was 49%. The full reasoning is in [`ontology/CHANGELOG.md`](ontology/CHANGELOG.md).

## ⚗️ Reagent classes

**In plain terms:** depositors write the same chemical a dozen different ways. "PEG 3350", "peg3350", "polyethylene glycol 3350" and "PEG 3,350" are one substance. The lexicon is the dictionary that maps every spelling onto one canonical name, and it is what makes the whole database queryable.

| Round | Reagents | Names | Component identification |
|---|---|---|---|
| Seeded from corpus mining | 147 | 501 | 79.1% |
| Round 1: 40 frequency-ranked decisions | 165 | 570 | 80.1% |
| Round 2: 10 grouped decisions, 1,004 names | 502 | 1,265 | 84.5% |
| Prose stripping (a parser fix, not curation) | 502 | 1,265 | 85.2% |
| Separating apparatus notes and bare units from reagents | 502 | 1,265 | 85.2% (87.5% on chemistry alone) |
| The 26 ionic liquids from PEG/Ionic Liquid 1 and 2 (v0.4.0) | 526 | 1,289 | 85.2% (87.5% on chemistry alone) |
| v0.5.0: stop aliasing one molecule to another | 535 | 1,300 | 85.3% |
| v0.5.1: twelve more aliases naming a different molecule | 542 | 1,306 | 85.3% |
| v0.6.0: the first gold set, 96 records labelled by hand | 556 | 1,346 | 85.3% |
| v0.6.1: the second gold set, sampled where teacher and pipeline disagree | 561 | 1,362 | 85.3% |
| v0.6.2: isomers the teacher's false positives exposed | 562 | 1,370 | 85.3% |
| v0.7.0: 29 prose fragments removed, 7 systematic names merged, 42 reagents added | 574 | 1,456 | 85.6% |
| v0.8.0: PEG MME de-fragmented, 20 duplicate ids merged | 557 | 1,467 | — |
| v0.8.1: all remaining duplicate ids merged, 5 invariants now tested | 517 | 1,463 | — |
| v0.8.2: leaked amount and method words merged onto their reagents | 509 | 1,462 | — |
| v0.8.3: Marc's review — formula ids renamed to names, 3 wrong aliases removed | 485 | 1,461 | — |
| v0.8.4: swept for aliases naming a different molecule — 4 more, now tested | 488 | 1,466 | — |
| v0.8.5: method text and element abbreviations out of ids, both now tested | 482 | 1,464 | 85.5% |
| v0.8.6: orphaned buffers restored, DTE separated from DTT | 486 | 1,484 | 85.7% |
| v0.8.7: AMS and BTPROP; narrative text out of the reagent denominator | 486 | 1,487 | 85.7% (88.3% on chemistry) |
| v0.9.0: the Morpheus stock table, 15 premixes from the vendor brochure | 499 | 1,543 | 85.9% (88.4% on chemistry) |
| v0.9.1: Tris IUPAC name, the DDT typo, MEGA8 suffix | 499 | 1,549 | — |
| v0.9.2: every pre-merge canonical id resolves again | **499** | **1,583** | **86.5%** (89.0% on chemistry) |

**For the crystallographer:** every reagent carries the chemistry the ontology needs, and each field is enforced on load rather than being optional documentation. A `peg` entry must state its molecular weight, a `buffer` must state its pKa, and a `premix` must list its constituents. Those invariants caught three separate attempts to bulk-add entries that could not satisfy them.

| Chemical class | Entries | What it carries |
|---|---|---|
| additive | 167 | ligands, cofactors and inhibitors carried into the drop |
| salt | 116 | Hofmeister rank, spanning sulfate (-4) to thiocyanate (+4) |
| peg | 91 | molecular weight, log-scaled; v/v below 600 and w/v above |
| buffer | 36 | pKa, since buffer identity collapses to the pH it sets |
| organic | 33 | alcohols and small organics reported v/v |
| polyol | 28 | glycerol, sugars and sugar alcohols |
| detergent | 16 | CYMAL-6, DDM, LDAO, beta-octyl glucoside: reported w/v, and rarely the precipitant |
| premix | 9 | constituent ids for Tacsimate, Morpheus and similar |
| other | 6 | vendor mixtures with no single molecular weight, such as PEG Smear Broad |

Two rounds of curation and one parser fix took identification from 79.1% to 85.2%. The marginal return has since fallen to roughly 15 components per decision, so further rounds are no longer the best use of expert time.

## 🎓 Training

**In plain terms:** the rule-based parser reads most of the archive, but some depositions are written in prose it cannot follow. A small language model was trained to read those, by learning from the hundreds of thousands of examples the rule parser already handles correctly. It costs nothing in hand-labelling, because the rule parser writes its own teaching material.

Progress per round, and what each has actually delivered, is tracked in [How the model is measured](#-how-the-model-is-measured).

**For the crystallographer:** SmolLM2-360M under MLX-LM LoRA on an M1 Max, prompt masked so the loss falls on the answer rather than on echoing the question, evaluated only on the residual the rule parser could not read.

Three findings shaped how it is trained, and two of them contradict the obvious approach:

- **Validation loss is the wrong stopping signal.** The labels are the rule parser's own output, so validation loss measures fidelity to the teacher rather than skill on the residual. It flattens by iteration 400 while identification is still climbing, and fidelity keeps improving to 91% long after identification has plateaued. Checkpoints are chosen by sweeping residual identification instead.
- **How long to train is unsettled, and three answers have now been wrong.** A plateau at ~600 iterations, measured against 36% duplicate rows. Then a full epoch. Then a frozen-benchmark sweep putting the peak at 2,000 — which is the version that reached this README, and it is wrong too. Against hand-labelled truth round 06's **6,000-iteration final adapter beats its own 2,000 checkpoint**: 99.6% precision to 95.7%, one false positive against twelve (p = 0.0034). Every one of those three answers came from `identification`, which cannot see a reagent that is real, present in the text, and not what the depositor meant. **Round 09 settled it, and the answer is "it barely matters".** A ten-point sweep across true iterations 1,000 to 8,000, scored on both gold sets pooled, moves recall by 1.7 points end to end with confidence intervals that overlap almost completely, while validation loss reached its floor at iteration 3,800 and stayed there. Longer is *not* better here, but neither is shorter: past roughly one thousand iterations the curve is flat, and any peak inside it is noise. The fourth answer is therefore to stop choosing on the score — iteration 4,500 ships because it sits mid-schedule and is most complete, not because it won. That the earlier three answers all came from `identification`, which cannot see a reagent that is real, present in the text, and not what the depositor meant, remains the reason they were wrong.
- **Gates are judged on the lower confidence bound**, so a lucky sample cannot pass them. An 800-record sweep once put two checkpoints on opposite sides of the identification gate whose intervals overlapped entirely.

Training data is deduplicated before oversampling: see [Redundancy](#-redundancy) for why that is not a detail. Every run is named `r1-parse-residual-smollm2-360m-roundNN` and logged to Weights & Biases, with the adapter directory carrying the same name so a checkpoint on disk traces back to the run that produced it.

## 📐 How the model is measured

**In plain terms:** it is easy to build a metric that a bad model passes. If you only ask "did it output a real chemical name", a model that answers *sodium chloride* to every question scores well, because sodium chloride is a real chemical. The measures below were chosen so that answering confidently and wrongly is penalised, not rewarded.

| Measure | Asks | Blind to |
|---|---|---|
| **Schema validity** | Is the output parseable JSON using only the allowed roles and units? | Whether any of it is true |
| **Identification** | Does each named reagent exist in the curated lexicon? | Whether it is the *right* reagent for this text |
| **Grounding** | Is each named reagent actually mentioned in the text it was given? | Whether the amount and unit are right |
| **Fidelity** | On text the rules *can* read, does the model produce the same components? | The residual, which is by definition harder |

None of those four has any ground truth behind it — they ask whether an answer is *defensible*,
not whether it is *right*. Precision, recall, F1 and F0.5, defined below, are computed against
hand-labelled truth and are the ones that decide anything.

**Identification is the weakest of the four and was quoted alone for too long.** A model reading `20% PEG 3350` and emitting `SODIUM_CHLORIDE` scores as identified: the name is real, it is simply not the reagent in front of it. Since the whole purpose of curation is to make names mean something, a measure that cannot tell a real name from the correct one is not measuring the thing that matters.

**Grounding closes that hole and needs no labels.** If the model names a reagent, some spelling of that reagent should appear in the source string. Punctuation and spacing are stripped on both sides, so `peg-3350`, `PEG3350` and `peg 3,350` all match one alias. A failure is either a hallucination or a spelling the lexicon has never seen, and both are worth knowing about, so ungrounded cases are written out for inspection rather than only counted.

**Every one of those four is precision-shaped, and that was the hole.** Each asks whether what the parser *said* is defensible. None can see a reagent it never mentioned at all, so a parser that reads one component per record and stops scores well on all four. Measured directly: on 4,000 residual records, 67% contain more reagent clauses than the parser emitted components.

### 🏅 The gold set: precision and recall against labelled truth

96 conditions were hand-labelled by a crystallographer in `app/gold_bench_v1.html`, one per screen, reagent names only. They are the yardstick, never training data, and they are excluded from every teacher run by name.

| Source | Precision | Recall | F1 | F0.5 |
|---|---|---|---|---|
| Rule parser alone | 98.3% | 79.6% | 88.0 | 93.9 |
| Rules + round 06 | **98.3%** | 95.6% | **96.9** | **97.7** |
| Rules + round 09 @ 4,500 *(shipped)* | 93.1% | **96.6%** | 94.8 | 93.8 |

*Random batch only. Both rows re-measured on 2026-08-04 against lexicon 0.9.2 and today's rules, so
they compare models rather than dictionaries; the pooled two-batch figures that decided which model
ships are [in the round 09 section](#-round-09-shipped-and-the-first-time-the-decision-rule-was-overruled).*

**Those precision figures are a property of the sample, not of the parsers.** A second, deliberately
contested batch puts the rules at **91.5%** and the shipped model at 91.9% — see
[the second gold batch](#-the-second-gold-batch-contested-records) below. On a randomly drawn record
the parsers are rarely challenged, so they rarely err.

**What each measure asks, and why there are two summary scores:**

| Measure | Asks | Fails when |
|---|---|---|
| **Precision** | Of the reagents we claim are in this condition, how many really are? | The parser invents chemistry |
| **Recall** | Of the reagents really in this condition, how many did we find? | The parser misses chemistry |
| **F1** | One number ranking two parsers, as the harmonic mean of the two above | Used alone: it treats a missed reagent and an invented one as equally bad |
| **F0.5** | The same, weighting precision twice as heavily as recall | The recall half is under-weighted: a parser that reads only the easy half of every condition can still score well, so it is read beside recall and never instead of it |

The harmonic mean rather than the average, deliberately: it punishes imbalance. A parser at 100%
precision and 20% recall averages 60% but scores **33** on F1, because a parser that says almost
nothing very carefully is not useful. That is the failure the older metrics could not see.

**F0.5 was the one to read for this project until 2026-08-04, and is now reported rather than
decisive.** The original argument was that a missed reagent is recoverable by re-reading the
deposition while an invented one propagates into everything built on the data. The first half of
that has not survived: nothing re-reads the deposition, so a missed reagent is simply absent, with
nothing in the dataset to mark its absence.

The second half rested on the extra false positives being *inventions*. They are not. Inspecting
them across rounds 07, 08 and 09, **none names a reagent absent from the source text** — they are
real reagents put in the wrong role, which is what the `protein_buffer` and `soak` roles were
added to fix. A misfiled component is visible and correctable; a missing one is neither.

**So F1 and recall decide, and F0.5 is kept in every table** so a reader who weights precision
more heavily can see what the choice cost. It costs 0.8 points of F0.5 and buys 39 reagents. The
two summaries disagree by more than they look, which is the reason both are always shown: round 06
leads round 08 by 1.0 on F1 and by **2.9** on F0.5.

**This is the number that settles what the model is for.** It adds **+20.1 points of recall** and finds 59 reagents the rules never reach, for 0.4 points of precision. The fidelity metric had made it look like an expensive imitation of `rules_v3`; against labelled truth it is reading a fifth of the corpus that the rules cannot.

It also confirmed the shape every audit had gestured at: **precision was never the problem.** One proposed reagent was removed across 96 records; 50 were added.

**For the crystallographer:** 96 records is a small yardstick and its intervals are wide (recall [88, 94]). It measures reagent *identity*, not concentration or unit. It does not replace the commercial screen cross-reference, where 38,481 conditions match a vendor-published formulation on every concentration, and it does not replace the human audit. What it does is make "did we miss something" answerable at all, which nothing in this project could do before.

### 🤗 Every round, and what it delivered

All nine rounds ship as LoRA adapters for `mlx-community/SmolLM2-360M-Instruct`, one directory
per round in **[`Dellboy/toppdblx-residual-parser`](https://huggingface.co/Dellboy/toppdblx-residual-parser)**. Each is 33 MB; the base model is not
redistributed. Rounds that delivered nothing are listed anyway.

| Round | What changed | Identification | Grounding | On gold, with the rules | Components shipped |
|---|---|---|---|---|---|
| [01](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round01) | Bootstrap distillation from rule output, lexicon 0.1.0.<br>3,000 iters × batch 16 = 48k examples · 32 layers · rank 8 · dropout 0 · **constant** LR 1e-4 | 87.0% † | not yet invented | — | N/A ‡ |
| [02](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round02) | `not_a_component` class added, confidence gate fixed.<br>1,200 × 16 = 19k · 32 layers · rank 8 · dropout 0 · constant LR 1e-4 | 89.7% † | not yet invented | — | N/A ‡ |
| [03](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round03) | Class rebalanced; **cosine decay and dropout introduced**.<br>1,000 × **32** = 32k · 32 layers · rank 8 · dropout 0.05 · cosine 1e-4→1e-5 | 88.4% † | not yet invented | — | N/A ‡ |
| [04](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round04) | Retrained on the 502-reagent lexicon.<br>1,000 × 16 = 16k · 32 layers · rank 8 · cosine 1e-4→1e-5 | **abandoned** — trained on 36% duplicate rows | — | — | N/A ‡ |
| [05](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round05) | Deduplicated training set, 95,818 distinct pairs.<br>1,000 × 16 = 16k ≈ **0.17 epochs** · 32 layers · rank 8 · cosine 1e-4→1e-5 | 87.58% | 93.41% | — | N/A ‡ |
| [**06**](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round06) | Full epoch, **rank 16**, 6,856 empty-answer examples.<br>6,000 × 16 = 96k ≈ **0.94 epochs** · **32 layers** · rank 16 · dropout 0.05 · cosine 1e-4→1e-5 | 89.05% final ◊, 90.52% at iter 2,000 | 93.15% ◊ | 95.3% / 85.7% ◈ | **153,736** |
| [07](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round07) | 32B-teacher labels **replacing** rules labels, 92.6% precise.<br>2,000 × 16 = 32k ≈ **0.23 epochs** · **16 layers** · rank 16 · cosine 1e-4→1e-5 | not run | not run | 93.6% / 89.1% | N/A ‡ |
| [08](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round08) | Same idea, labels filtered to 97.6% precise, trained 4× longer.<br>8,000 × 16 = 128k ≈ **1.06 epochs** · **16 layers** · rank 16 · cosine 1e-4→1e-5 | not run | not run | 95.3% / 89.1% | N/A ‡ |
| [**09**](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round09) | **Shipped 2026-08-04.** Teacher labels **added, not substituted**; scope roles; prompt v2; lexicon 0.9.2.<br>8,000 × 16 = 128k ≈ **1.04 epochs** · **32 layers** · rank 16 · dropout 0.05 · cosine 1e-4→1e-5 · warmup 50 · **shipped at iter 4,500** | 95.28% ◊ ¶ | 94.83% ◊ ¶ | **92.5% / 92.0%** ◈ | pending ‖ |

Every round: `--mask-prompt` (loss on the completion only), `max_seq_length` 1024, LoRA scale 20,
base `mlx-community/SmolLM2-360M-Instruct` on an M1 Max. Epochs are given only where the training
set size at the time is recorded; `iters × batch` is exact for every round and is the honest
comparison.

† Scored against a **live residual** that shrank from the easy end every time curation improved,
so these three are not comparable to each other or to anything else. From round 05 the benchmark
is frozen.

◊ Re-measured on 2026-08-04 against **lexicon 0.9.2**, so the identification and grounding columns
compare models rather than dictionaries. Round 06's identification moved from its published 88.99%
to 89.05% under a lexicon that shrank from 502 reagents to 499 — a 0.06-point change, which is the
evidence that the curation rounds did not quietly move the goalposts.

◈ **Both gold batches pooled** (192 records, 616 reagents), measured 2026-08-04 at lexicon 0.9.2
with today's rules. Rows 07 and 08 are single-batch figures at lexicon 0.6.x and are **not**
comparable to these two, or to each other after 0.7.0 taught `apply_slm` to resolve aliases.

¶ Measured on round 09's **final** adapter (true iteration 8,000). The shipped checkpoint is
iteration 4,500; its own frozen-benchmark figures are being measured and this cell will carry them.

‖ Round 09 was chosen on 2026-08-04 and the corpus has not yet been re-generated with it — that is
a 4.7-hour pass. Until it completes the released dataset still carries round 06's 153,736
components.

**Three cells need explaining, because each is a real answer rather than a gap.**

**"Not yet invented"** is literal. The grounding check — does the reagent the model named actually
appear in the text it was given — was written on 2026-07-31; rounds 01 to 03 were trained the day
before. Their adapters are still on the Hub and could be re-scored, but the number would sit beside
an identification figure measured against a residual that no longer exists, which would look
comparable and would not be.

**"Not run"** for rounds 07 and 08 is a decision, not an omission. By then the hand-labelled gold
set existed, and `identification` had already produced three wrong answers to "how long should this
train" — it asks only whether an emitted name is *in the lexicon*, so a model inventing plausible
chemistry scores well on it. Those rounds were measured against labelled truth instead, which is
the stricter test.

**‡ "N/A" rather than "0", and the difference matters.** The column counts rows that reached the
released dataset. **Only round 06 has ever been applied to the corpus** — round 09 is now the
shipped model but its corpus pass has not yet run, and every other adapter
is a training experiment that was measured and set aside, so it was never *given* the residual
to read. A zero would say "it ran and found nothing", which is a claim about the model; N/A says
"it was never asked", which is the truth. Producing a real number for rounds 07 and 08 would
cost **4.7 hours of generation each** — measured from round 06's own 282-minute run, not
estimated — and would answer a different question than the column asks.

### 🏆 Round 09: shipped, and the first time the decision rule was overruled

The last training round this project will do, and the first clean test of the question rounds 07
and 08 were meant to answer: **does teacher distillation help when the labels are *added* rather
than substituted, at matched capacity?** It does.

| | |
|---|---|
| **Labels** | Every rules label kept, plus ~3,400 teacher-labelled residual rows **added, never substituted** — which is what rounds 07 and 08 got wrong |
| **Capacity** | **32 LoRA layers**, matching round 06. Rounds 07 and 08 ran at 16 because `--num-layers` was left at its default |
| **Prompt** | `SYSTEM_V2`, adding the `protein_buffer` and `soak` roles. A round trained on v2 must be *served* with v2 |
| **Data** | 122,469 rows at lexicon 0.9.2, with 24,040 `protein_buffer` and 16,272 `soak` targets |
| **Gate** | The build **fails rather than trains** if a single gold record leaks, or if any example would exceed the 1,024-token cap. Verified after the fact: **zero** gold records reached training |

#### The result, on both gold sets pooled

The two 96-record gold sets are pooled into one 192-record, 616-reagent evaluation rather than
averaged. Averaging two F0.5 scores weights a small set equally with a large one and discards the
raw counts; pooling `tp`/`fp`/`fn` gives one number with a real confidence interval.

| Model | found | missed | false | recall | precision | F1 | F0.5 |
|---|---|---|---|---|---|---|---|
| Round 06 *(previously shipped)* | 528 | 88 | 26 | 85.7% | **95.3%** | 90.26 | **93.22** |
| **Round 09 @ 4,500** *(shipped)* | **567** | **49** | 46 | **92.0%** | 92.5% | **92.27** | 92.41 |

**Round 09 finds 39 more real reagents and misses 44% fewer**, for 20 more false positives.

#### Why F0.5 was overruled

On the metric this README has argued for throughout, **round 06 wins by 0.8 points** — and it is
still shipped second. That needs saying plainly rather than quietly restating the criterion.

F0.5 weights precision twice as heavily as recall. That is the right weighting for a released
dataset when the failure being guarded against is *invented chemistry*. It is the wrong weighting
here, for a reason that only became visible with the errors in front of us: **round 09's extra
false positives are not inventions.** Every one names a reagent genuinely present in the source
text — a protein storage buffer, a soak, a cryoprotectant — placed in the wrong role. The same was
true of rounds 07 and 08. A metric tuned to punish hallucination is being paid to punish
misattribution, which is a different and much less serious failure, and one the `protein_buffer`
and `soak` roles now exist to fix.

Against that, 88 missed reagents is chemistry absent from the database with nothing to mark its
absence. **A missing component is invisible; a misfiled one is findable and correctable.** For a
dataset whose purpose is completeness of crystallisation chemistry, that asymmetry runs the
opposite way to F0.5's.

So the decision rule is changed rather than bent: **round 09 ships on recall and F1, and F0.5 is
demoted from decision metric to reported metric.** Both are in the table above; a reader who
weights precision more heavily can see exactly what that choice cost.

#### Which checkpoint, and why not the best-scoring one

A ten-point sweep across true iterations 1,000 to 8,000, scored on both gold sets:

| true iter | 1,000 | 2,000 | 3,000 | 3,500 | 4,000 | **4,500** | 5,000 | 6,000 | 7,000 | 8,000 |
|---|---|---|---|---|---|---|---|---|---|---|
| recall | 91.6 | 90.3 | 89.9 | 90.9 | 90.4 | **92.0** | 91.4 | 91.1 | 91.7 | 90.3 |
| F1 | 92.31 | 90.92 | 89.07 | 91.88 | 91.54 | **92.27** | 92.14 | 92.04 | 92.24 | 91.75 |

Ignoring iteration 3,000, which is a genuine outlier, **the sweep is flat** — 1.7 points of recall
separating eight checkpoints whose confidence intervals overlap almost completely. Iteration 1,000
edges F1 by 0.04 and iteration 4,500 edges recall by 0.4; neither is a real difference.

**Iteration 4,500 is shipped, chosen on robustness rather than on the score.** It is the most
complete on the criterion that now decides, it sits mid-schedule where the cosine LR has settled
rather than at either end, and it is not one of the pre-resume checkpoints from the interrupted
run. Picking iteration 1,000 on a 0.04-point F1 edge would be fitting noise, and it is exactly the
mistake this project already made once when a frozen-benchmark sweep promoted round 06's
checkpoint 2,000 over its final adapter.

**The flatness is itself the answer to "how long should this train".** Validation loss reached its
floor of 0.0040 at true iteration 3,800 and stayed there; downstream accuracy moved by less than
its own noise from iteration 1,000 onward. The last 3,500 iterations bought nothing measurable.

**"Regressed" describes rounds 07 and 08 accurately and explains nothing.** They scored below round
06 on gold, which is a fact. What caused it is not established: both ran at **16 LoRA layers**
against round 06's 32, because `--num-layers` was left at its default, so they changed the label
source *and* halved the adaptable capacity in the same experiment. The teacher-label hypothesis has
never been tested at matched capacity, and the word "regressed" in this table should be read as
"scored worse", not as "distillation does not work".

**Round 06 was the shipped model until 2026-08-04 and is superseded by round 09; its final adapter
remains the one to use *within* round 06, and its 2,000-iteration checkpoint is kept beside it because the disagreement is the interesting part.** The frozen-benchmark sweep promoted
checkpoint 2,000 over the final adapter on *identification*, and against hand-labelled truth that
was wrong: the checkpoint makes twelve false positives where the final adapter makes one
(p = 0.0034). Identification asks only whether an emitted reagent exists in the lexicon, so it
cannot see a name that is real, present in the text, and simply not what the depositor meant — the
exact failure a checkpoint trained a little too long produces.

**And the released data was never affected**, by accident rather than design. `apply_slm` accepted
`--checkpoint` and silently ignored it until 2026-08-02, so the 163,353 components shipped on
2026-08-01 came from the final adapter, which is the better model. The flag now works; the notes
that said the release used checkpoint 2,000 were wrong and are corrected.

### Rounds 07 and 08: distillation, and a measurement that was wrong

Two attempts to train past the rule parser's ceiling using labels from a local Qwen2.5-32B teacher
instead of `rules_v3`. Both scored worse than round 06 — and then the metric that said so turned
out to be biased against them.

**Five of the 96 gold records carried a label the lexicon could not place**, so their truth set was
missing a reagent, and a parser naming that reagent correctly scored a *false positive*. 3KDJ is
the clearest case: the text reads `0.01M GSH/GSSG`, the model answered `GLUTATHIONE`, which is
right, and it counted against it. That penalty lands hardest on whichever model says the most —
a bias in favour of the quieter one, which is precisely backwards for a metric whose purpose is
to measure recall.

Lexicon 0.6.0 resolves 18 of the 25 unresolvable labels; the remaining seven are labelling
artefacts rather than lexicon gaps (`GSMT` is the protein, `POLYAMINES` a class, `BSI100156` a
compound code), so those records are now excluded from scoring and counted. Re-scored:

| | precision | recall | F1 | F0.5 |
|---|---|---|---|---|
| rules only | 100.0% | 67.7% | 80.7 | 91.3 |
| **round 06** | **99.6%** | 87.4% | **93.1** | **96.9** |
| round 07 — teacher labels 92.6% precise, 0.23 epochs | 93.6% | **89.1%** | 91.3 | 92.6 |
| round 08 — labels 97.6% precise, 1.06 epochs | 95.3% | **89.1%** | 92.1 | 94.0 |

> **Measured at lexicon 0.6.x, and left there deliberately.** Every row was scored before
> lexicon 0.7.0, which added 42 reagents and taught `apply_slm` to resolve aliases — worth
> about +5 points of recall to any row containing the student. Re-scoring round 06 alone
> would make it beat rounds 07 and 08 on a change none of them received, so the comparison
> is kept internally consistent at the version it was run. The current shipped figures are
> [above](#-the-gold-set-precision-and-recall-against-labelled-truth).

Read on F0.5 — the summary that weights precision twice as heavily, and the right one for a
released dataset — round 06 leads by 2.9 points rather than F1's 1.0.

Round 06 still leads on F1 and holds a 4.3-point precision advantage, so **it remained the shipped
model at the time**. But the gap is smaller than first reported, both teacher rounds do gain real recall, and
the line of work is **unresolved rather than closed** — an earlier version of this section
declared it closed on the strength of the biased measurement.

**Round 09 closed it.** Given the same teacher labels *added* to the rules labels rather than
replacing them, and 32 LoRA layers rather than 16, distillation gains 6.3 points of recall over
round 06 while giving up 2.8 of precision. The recall gain rounds 07 and 08 showed was real; what
they got wrong was throwing the rules labels away and halving capacity in the same experiment.

**The remaining errors are misattribution, not invention.** Of round 08's false positives, *none*
name a reagent absent from the text. They are reagents genuinely present but not part of the
crystallisation condition: a protein storage buffer (`PROTEIN SOLUTION CONTAINING 50MM HEPES`), a
soak, a cryo step. That is a far more tractable target than hallucination, and it is a question
about *roles* rather than chemistry.

### Agreement gating across two teachers

The errors are systematic rather than random — 11 of round 08's 14 false positives are the same
reagents as round 07's, across independently filtered labels and four times the training.
Correlated single-model error is what independent-model agreement removes and what heuristic
filters cannot, so a teacher-only find is kept only when a second, architecturally different 32B
names the same reagent unprompted. Gemma-4-31b, not another Qwen: `chem_sage` is Qwen2-based and
would share the first teacher's failure modes.

| | precision | recall | F1 | F0.5 |
|---|---|---|---|---|
| rules + student *(round 06, shipped)* | **99.6%** | 87.4% | 93.1 | **96.9** |
| rules + Gemma alone | 96.3% | 87.8% | 91.8 | 94.4 |
| + every Qwen find | 92.2% | **92.9%** | 92.5 | 92.4 |
| **+ only where Qwen and Gemma agree** | 96.1% | 91.8% | **93.9** | 95.2 |

> **Measured at lexicon 0.6.x, and left there deliberately.** Every row was scored before
> lexicon 0.7.0, which added 42 reagents and taught `apply_slm` to resolve aliases — worth
> about +5 points of recall to any row containing the student. Re-scoring round 06 alone
> would make it beat rounds 07 and 08 on a change none of them received, so the comparison
> is kept internally consistent at the version it was run. The current shipped figures are
> [above](#-the-gold-set-precision-and-recall-against-labelled-truth).

**The mechanism works.** Requiring two independent teachers to agree keeps **13 of the 16 correct
finds** while cutting the wrong ones **from 22 to 10**, which lifts precision on the combined
output from 92.2% to 96.1% for 1.1 points of recall. That is the best **F1** measured anywhere in
this project — 93.9 against the shipped model's 93.1, with 4.4 more points of recall.

**It still does not clear the bar.** On F0.5, the summary that matches how a released dataset is
actually used, round 06 leads 96.9 to 95.2, and on raw precision 99.6% to 96.1%. Agreement removes
about half the misattribution, not all of it.

Two further findings:

- **Gemma is *not* the better teacher, and an earlier version of this section said it was.**
  That claim compared a *rules + Gemma* row against a Qwen figure measured on a different set
  of records. Scored like for like — the 79 gold records both models answered and whose
  labels resolve — Qwen wins on precision at identical recall:

  | | precision | recall | F1 | F0.5 | false positives |
  |---|---|---|---|---|---|
  | Gemma-4-31b | 89.7% | 87.9% | 88.8 | 89.4 | 24 |
  | **Qwen2.5-32B** | **93.3%** | 87.9% | **90.5** | **92.2** | **15** |

  Both find exactly the same reagents (210 true positives, 29 misses each); Gemma simply
  asserts nine more things that are not there. It is also the less reliable harness: 82 of
  96 generations parsed against Qwen's 96 of 96, because its reasoning block eats the token
  budget. Gemma was chosen for **architectural independence**, which is what the agreement
  gate needs and which it delivers — not for being stronger.
- **It is still too noisy to train on.** The surviving additions are 13 correct against 10 wrong,
  a 43% error rate on the new signal, and round 08 established that this student absorbs label
  noise rather than averaging it out.

So the result lands as an **inference-time ensemble**, not a training recipe: run the second
teacher only on the teacher-only candidates rather than the corpus, and take what both assert.
Whether it ships is a judgement rather than a measurement — F1 says yes, F0.5 says no, and this
project has consistently preferred precision.

**A reasoning model needed two harness fixes**, both of which would have failed silently. Gemma-4
emits a `<|channel>thought` block before answering, so at the 448-token budget tuned for Qwen only
6 of 96 generations ever closed a JSON array; and because it quotes the few-shot examples verbatim
while reasoning, the *first* JSON array in its output is the prompt being thought about rather than
the reply. Taking the first array would have scored our own examples back as the teacher's labels
and looked entirely plausible.

### 🥊 The second gold batch: contested records

The random 96 answered "how good is the pipeline" and could not answer "which source is better",
because only 41 of its 91 scored records contained a reagent the sources disputed at all. A second
96, sampled where the teacher and the pipeline **disagree**, in three equal strata — teacher found
something extra, pipeline found something extra, both did.

It produced **32 rejections against the random batch's 1**, which is the precision signal the first
set could not supply.

| source | precision | recall | F1 | F0.5 |
|---|---|---|---|---|
| rules only | 91.5% | 60.2% | 72.7 | 82.9 |
| rules + student *(shipped)* | 92.2% | 76.7% | 83.7 | 88.6 |
| rules + 32B teacher | 90.7% | **87.3%** | 88.9 | **90.0** |
| union of both | 90.8% | **97.5%** | **94.0** | 92.0 |
| rules + only where both agree | **92.2%** | 66.5% | 77.3 | 85.6 |

**Three corrections come out of it.**

**The rules are not 100% precise.** They are **91.5%** here. The random batch
reported 100.0% because it never drew a contested record, and that figure was quoted repeatedly,
including in this README and on the published model card.

**The teacher beats the student on recall, decisively.** It finds **75 reagents the student misses
against 35 the other way, p = 0.00017** — the first unambiguous result in this line of work — and
pays almost nothing for it: 11 extra false positives against 3 avoided, p = 0.057.

**The student's precision advantage largely evaporates**: 92.2% against the teacher's 90.7%, not
98.2% against 91.5%. On a random sample the student looks far more precise mostly because it rarely
says anything contestable.

**Both batches are needed, and neither alone is honest.** This one is adversarial by construction,
so its absolute numbers do not describe the corpus — 57% of teacher-labelled records are contested,
not all of them. The random 96 remains the yardstick for *how good is the pipeline*. But comparisons
between sources are what this batch measures well, and it says the earlier reading — teacher labels
buy +2 recall for −4 precision — came from a sample where contested records were too rare for the
trade to show its real shape.

## 🔁 Redundancy

**In plain terms:** the same experiment appears in the archive over and over. Popular proteins are solved hundreds of times, and a successful recipe gets copied. One condition string in this corpus appears **1,784 times**. That sounds harmless, and it quietly corrupts three different things if you let it.

| Where it bites | Left alone | Handled |
|---|---|---|
| Expert curation | 1,484 near-identical records to judge | 10 grouped decisions covering 1,004 names |
| Train and test splits | The same protein on both sides, so accuracy measures memory | Split by sequence cluster, leak-free at 30%, 50% and 90% |
| Training data | 45.7% of rows repeated; one string took 1.3% of all training | Deduplicated: 135,793 rows to 95,818, all distinct bar a deliberate 8x |
| Validation loss | Scored on duplicated conditions | 17,292 rows to 12,730, fully distinct |

**For the crystallographer:** the corpus is not a sample of crystallisation space, it is a sample of *what people deposited*, and deposition is heavily skewed. A single 30% identity cluster holds up to 2,876 entries. Lysozyme, trypsin and Fab fragments alone would dominate any statistic computed per record.

Three consequences, each of which had to be designed for rather than discovered late:

- **Splits are by sequence cluster, never by entry** (spec 7.3). Splitting at random would put near-identical proteins on both sides and every metric would measure memorisation. The folds are the connected components of the union of all three clustering thresholds, because MMseqs2 clusters do not nest: 68 clusters at 90% identity straddled a 30% split until that was fixed.
- **Audits rank distinct decisions, not instances.** Judging "20% PEG 3350 becomes PEG_3350" for the thirty-thousandth time buys no evidence. Each question stands for every occurrence of the string it names.
- **Training data is deduplicated before oversampling, not after.** This one was caught late: 36% of training rows were exact duplicates, and because the rare `not_a_component` records were themselves duplicated, multiplying them first produced an effective 11.7x where 8x was specified. Deduplicating the validation set matters more still, since a repeated condition there distorts the loss curve used to judge the run.

The general lesson is that a redundant corpus punishes anything counted per record. Every figure in this README that could be inflated by repetition is either computed over distinct strings or over sequence clusters, and says which.

## 🧪 Curation interfaces

Expert time is the scarcest input, so every audit is reduced to **tens of decisions ranked by corpus reach**, never a row-per-instance review.

| Round | Questions | Reach |
|---|---|---|
| Reagent lexicon, rounds 1 and 2 | 35, then 13 | Seeded the original lexicon |
| Lexicon gaps | 40 | 7,018 unidentified components |
| Lexicon gaps, grouped | 10 | 1,004 names, 34% of the unidentified mass |
| Classification accuracy, rounds 1 and 2 | 96 each | The per-class accuracy number (spec 6.6), split by provenance |
| **Gold set** | **96** | **Precision and recall against labelled truth, the yardstick for every future round** |

`app/condition_courtroom_v5.html` renders any payload with the same shape, so a new audit is a new generator stage rather than a new page. `condition_courtroom_v7.html` is the accuracy audit: one condition per screen, one checkbox, and a three-field diagnosis that appears only when a card is ticked, so the ~90% waved through stay a single click. `gold_bench_v1.html` is the labeller: deposition text, removable reagent chips, and an autocomplete over all 535 lexicon names for what the pipeline missed.

**A recommendation is an answer, not a suggestion.** Every dropdown is pre-set and there is an accept-all button, so a wrong recommendation is not caught, it is accepted in bulk. Generators therefore carry explicit chemistry guards: numbers must match exactly (a number in a reagent name is molecular weight or substitution position, never decoration), element and acid words must agree, and a family name such as bare `peg` identifies to nothing rather than inventing a member. Guards decide what is **recommended**, never what is **available**.

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
| `parse.lexicon_coverage` | Measures what share of the corpus the lexicon identifies |
| `parse.run_parser` | The deterministic rule parser, over the whole archive |
| `assign.build_screens` | Extracts screen formulations verbatim from vendor PDFs |
| `assign.screen_match` | Matches parsed conditions to screen wells, schema to schema |
| `link.representative` | Chooses one sequence per entry, never one per chain |
| `link.cluster` | MMseqs2 clustering at three identity thresholds |
| `link.sifts`, `link.uniprot` | PDB to UniProt mapping, and full-length reference sequences |
| `parse.lexicon_questions` | Ranks unidentified names by corpus mass into a few dozen curation calls |
| `parse.apply_lexicon_answers` | Folds audit answers back into the lexicon, with the reasoning recorded |
| `parse.curation_queue` | Groups the remaining gaps into ten bucketed decisions |
| `parse.apply_curation_queue` | Applies a bucketed answer to every name in its bucket |
| `assign.classify` | The seven JCSG Top96 precipitant classes, or Unclassified with a reason |
| `eval.class_audit` | Accuracy audit, 96 conditions split by provenance, one decision each |
| `eval.gold_questions` | Samples 96 residual records for hand labelling, banded by text length |
| `eval.gold_metrics` | Precision, recall and F1 against the gold set, per source and unioned |
| `eval.splits` | Cluster-based train, validation and test splits, leak-free at all three thresholds |
| `eval.baselines` | Frequency prior and homology retrieval, the baselines any model must beat |
| `models.build_slm_dataset` | Bootstrap training data for the residual parser, no hand labelling |
| `models.train_slm` | LoRA fine-tune under MLX-LM, W&B logged, one named run per round |
| `models.eval_slm` | Fidelity and residual identification, with Wilson intervals on both gates |
| `models.apply_slm` | Runs the model over the residual and writes the components it reads |
| `models.teacher_label` | Labels the residual with a local 32B, to train past the rule parser's ceiling |
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
| Depositor errors are reproduced, not corrected | A handful of records state nanomolar concentrations of bulk reagents. The exception is an amount that cannot be true at all (above 8 M equivalent, or above 100%): the reagent is kept, the amount dropped, and the record flagged `implausible_concentration` |
| Screen matching cannot validate reagent naming | Both sides use the same lexicon, so a systematic naming error moves both identically |
| R1 labels come from the rule parser | Fidelity to `rules_v3` is a ceiling, not evidence of beating it: only residual identification, where no label exists, measures a real gain |
| The residual parser cannot discover new chemistry | It emits only names it has seen, so a genuinely absent reagent looks the same as a model error. Closing that gap is curation, not modelling |
| The gold set is 96 records | Wide intervals (recall [88, 94]), reagent identity only, no concentrations. It makes "did we miss something" answerable, not precisely answerable |
| Recall is the weaker half | 99.6% precision against 91.5% recall: the parser is far likelier to miss a reagent than to invent one, and every headline metric before the gold set was blind to that |

## ✅ To Do

- [x] Ingest the whole archive, with a byte-level fidelity gate
- [x] Deterministic rule parser with a stable discard taxonomy
- [x] Commercial screen library, extracted verbatim from vendor sources
- [x] Sequence linkage and MMseqs2 redundancy control
- [x] Assemble the release in five formats, with a generated datasheet
- [x] Settle licensing: CC-BY-4.0 data, MIT code
- [x] Complete the 90 GB archive snapshot and run the full-scale fidelity check
- [x] Reagent lexicon: two curation rounds plus the ionic liquids, 147 to 526 reagents (499 today, after merging what was never distinct)
- [x] Seven-class condition ontology, replacing the withdrawn three-level version
- [x] Fine-tune SmolLM2 on the parse residual, and strip narrative prose in the parser
- [x] Classification accuracy audit, two rounds of 96 (spec 6.6): 85.4% rules-derived, 83.3% model-derived, pooled
- [x] Apply the trained model over the residual: 153,736 components, classified coverage 59.5% to 80.2%
- [x] Gold set of 96 hand-labelled records, and the first precision/recall this project has had
- [x] Second gold set of 96, sampled where the pipeline and the teacher disagree
- [x] Add the reagents the gold sets named that the lexicon could not place
- [x] Tell the drop from the protein sample: `protein_buffer` and `soak` roles, 6,800 components
- [x] De-duplicate the lexicon: PEG MME was seven canonical ids per molecular weight; 36 merged, five invariants now tested
- [x] Remove the aliases naming a *different* molecule — barium/yttrium, CAPS/CHAPS, DTT/DTE and four more
- [x] Transcribe the Morpheus stock table from the vendor brochure: 15 premixes with real compositions
- [x] Classify premixes on their constituents rather than refusing them: coverage 76.8% to 80.2%
- [x] Split reagents written with no separator at all: +7,553 identified components, nothing lost
- [x] **Teacher distillation, resolved:** round 09 adds the 32B's labels to the rules labels instead of replacing them, at 32 LoRA layers rather than 16. **+6.3 points of recall over round 06**, 39 more reagents found on the pooled gold sets. Rounds 07 and 08 failed because they changed the label source *and* halved capacity in one experiment
- [x] **Ship round 09 and change the decision metric to match the goal:** F0.5 preferred round 06, and was overruled because the extra false positives are misattributed real reagents rather than inventions, while missed reagents are simply absent. Recall and F1 now decide; F0.5 stays in every table
- [x] **Ten-point checkpoint sweep across true iterations 1,000 to 8,000**, both gold sets, pooled. The curve is flat — validation loss floored at iteration 3,800 and downstream accuracy moved by less than its own noise from 1,000 onward, so the last 3,500 iterations bought nothing
- [ ] **Re-generate the corpus with round 09** (a 4.7-hour pass), then re-run classification, screen matching and the datasheet. Until this lands the released data is still round 06's
- [ ] Cut the 49 reagents round 09 still misses, and the 46 it misfiles: the misfiles are role errors the `protein_buffer` and `soak` classes should absorb
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
