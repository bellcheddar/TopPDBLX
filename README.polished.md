# 🔮 TopPDBLX

> **Every crystallisation condition in the Protein Data Bank, parsed, normalised and linked to the sequence that produced it.**

![python](https://img.shields.io/badge/python-3.14-3776AB?logo=python&logoColor=white) ![records](https://img.shields.io/badge/records-199%2C185-467FF7) ![components](https://img.shields.io/badge/components-610%2C076-467FF7) ![parse coverage](https://img.shields.io/badge/parse%20coverage-93.5%25-00897B) ![archive fidelity](https://img.shields.io/badge/archive%20fidelity-100%25-00897B) ![tests](https://img.shields.io/badge/tests-400%20passing-00897B) ![data](https://img.shields.io/badge/data-CC--BY--4.0-9b51e0) ![code](https://img.shields.io/badge/code-MIT-9b51e0) ![phase 0](https://img.shields.io/badge/phase%200-complete-fcb900) ![phase 1](https://img.shields.io/badge/phase%201-complete-fcb900) ![author](https://img.shields.io/badge/author-Marc%20C.%20Deller%2C%20D.Phil.-1C244B)

<table>
<tr>
<td>🌐 <b>Website</b></td><td><a href="https://marcdeller.com" target="_blank" rel="noopener noreferrer">marcdeller.com</a></td>
<td>✉️ <b>Contact</b></td><td><a href="mailto:marc@marcdeller.com">marc@marcdeller.com</a></td>
<td>🐙 <b>GitHub</b></td><td><a href="https://github.com/bellcheddar/TopPDBLX" target="_blank" rel="noopener noreferrer">bellcheddar/TopPDBLX</a></td>
</tr>
</table>

---

The Protein Data Bank holds about 200,000 crystallisation recipes, and every one was typed in free-hand by a different scientist, in no agreed format. TopPDBLX recovers structured, curated chemistry from that text: it turns the free-text `_exptl_crystal_grow.pdbx_details` field into typed components (reagent, concentration, unit, role), cross-references them against published commercial screen formulations, and attaches MMseqs2 cluster identifiers so that redundancy can be controlled properly.

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
| **Reads what the rules cannot** | A 32B teacher labels the hard records; a 360M student learns from it and reads every residual record the rule parser gives up on. Recall against hand-labelled truth: 69.5% (rules alone) → 92.0% (rules + round 09, pooled) |

## 📖 The words on this page

Crystallography and machine learning each bring their own vocabulary, and this page uses both.
Everything here is defined where it first matters, but this is the short version.

| Term | What it means here |
|---|---|
| **Crystallisation condition** | The recipe a protein was crystallised from: a few reagents, their concentrations, and a pH. This project parses about 200,000 of them |
| **Deposition text** | What the scientist actually typed into the PDB, free-hand, in no agreed format. The raw material |
| **Precipitant** | The reagent doing the work: it pulls the protein out of solution. Usually a PEG, a salt or an organic |
| **Drop / reservoir** | The two halves of a vapour-diffusion experiment. The drop holds protein plus condition; the reservoir pulls water out of it |
| **Mother liquor** | The solution a crystal grew in and sits in |
| **Cryoprotectant** | Added *after* growth to stop ice forming when the crystal is frozen. Real chemistry, but it did not crystallise anything |
| **Premix** | A vendor mixture sold as one bottle (Morpheus "Divalents", Tacsimate) that is really several reagents |
| **PEG** | Polyethylene glycol, the commonest precipitant. The number is its molecular weight, not an amount: "PEG 3350" is a size |
| **Hofmeister rank** | An ordering of salts by how strongly they push proteins out of solution |
| | |
| **Lexicon** | The dictionary mapping every spelling a depositor might use onto one **canonical id**. `peg3350`, `PEG 3,350` and `polyethylene glycol 3350` all become `PEG_3350` |
| **Canonical id** | The single name this project uses for a reagent, in `UPPER_SNAKE_CASE` |
| **The residual** | The records the rule parser could **not** read. This is what the language model is for, and it is never trained on them |
| **The gold sets** | Two batches of 96 records each, 192 in total, labelled by hand in `app/gold_bench_v1.html`. The only ground truth here, used to measure and never to train |
| **LoRA** | A way of fine-tuning a model by training a small adapter rather than all its weights. Ours is 33 MB against a 360M-parameter base |
| **Distillation** | Training a small model on a larger one's output. Here a 32-billion-parameter *teacher* labels text for a 360-million-parameter *student* |
| **Grounding** | Checking that a reagent the model named actually appears in the text it was given. Catches invention, which no accuracy score can |
| **Identification** | Whether an emitted name exists in the lexicon. A weaker test than grounding: it doesn't check that the name is *right* for this text, only that it's real |

## 🔄 Workflow

**In plain terms:** the same chemical appears in the archive as "PEG 3350", "peg3350" and "polyethylene glycol 3,350", amounts are written in a dozen notations, and some entries are whole paragraphs of narrative. The work is to turn that text into a table you can query, without inventing anything that is not there.

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
          │                          │              192 hand-labelled records  │
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

### 🤗 The teaching loop, and why there are two models

**Why bother with two models: the teacher is roughly 90x larger and far slower to run.** At around 40 seconds per record, a 32B model would take weeks to read every residual record the rules cannot parse; the 360M student reads them in under two hours. So the teacher labels a few thousand of the hardest cases, and the student does the corpus-scale work, which took recall against hand-labelled truth from **69.5% (rules alone) to 92.0% (rules + round 09, pooled)**.

**In plain terms:** a small model is cheap to run over 200,000 records but not clever enough to teach itself. A large model is clever enough to teach but far too slow to run over the whole archive. So the large one reads a few thousand of the hard cases, the small one learns from what it produces, and the small one does the actual work. The expert never labels a corpus: they label a couple of hundred records that decide whether any of it worked.

The cycle has three participants and each does the one thing it is best at:

| | Does | Costs | Why it cannot do the others' job |
|---|---|---|---|
| **Expert** (`gold_bench`, `courtroom`) | Labels the gold sets; answers curation questions | An hour or two | Cannot label the whole residual, and should never be asked to |
| **Teacher** (Qwen2.5-32B, local) | Labels a few thousand of the hardest residual records | ~40 s/record | Far too slow for the corpus, and noisier than the student it trains |
| **Student** (SmolLM2-360M) | Reads every residual record | ~0.1 s/record | Bootstrapped from the rules, so the rules were its ceiling until the teacher's labels lifted it |

**Why the expert sits at the top of the loop and not inside it.** Every automated measure in this project is precision-shaped: it can catch an invented reagent, but nothing automated can catch one that was never mentioned at all. The gold sets are the only thing that can, so they are the yardstick and are never trained on, and every teacher run excludes them by name.

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
| `gold_bench_v1.html` | Single-file labeller for the gold records that judge every model round |

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
| Parser accuracy | Both gold-set batches pooled, rules + round 09: **92.5% precision, 92.0% recall** (rules alone: 95.1% precision, 69.5% recall) |
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

**Premixes are no longer refused, and that is a reversal.** Spec 6.4 was settled by declaring every premixed system Unclassified, which was right while a premix was an opaque token. Once the vendor compositions were transcribed (lexicon v0.9.0) it stopped being right: 59% of the conditions it refused were blocked by a premix made only of *buffers* (MES/imidazole, phosphate-citrate, MIB, SPG), and a buffer is excluded from naming the class anyway. Those were ordinary conditions declined for their packaging rather than their chemistry. A premix now contributes the chemistry it is made of, so Morpheus Precipitant Mix 4 is MPD with PEG 1000 and PEG 3350, which is `Organic/PEG`. Classified coverage moved 76.8% to **80.2%**. A premix with no transcribed composition is still Unclassified, because there is nothing to expand it into.

An earlier three-level ontology of 163 binned groups was withdrawn at v0.3.0. Its groups were derived by binning the corpus and then having labels retrofitted, which spec 6.1 rejects in its first line, and several were not chemically coherent: median purity across the 41 second-level groups was 49%. The full reasoning is in [`ontology/CHANGELOG.md`](ontology/CHANGELOG.md).

## ⚗️ Reagent lexicon

**In plain terms:** depositors write the same chemical a dozen different ways. "PEG 3350", "peg3350", "polyethylene glycol 3350" and "PEG 3,350" are one substance. The lexicon is the dictionary that maps every spelling onto one canonical name, and it is what makes the whole database queryable.

| Stage | Reagents | Names | Identified |
|---|---|---|---|
| Seeded from corpus mining | 147 | 501 | 79.1% |
| Round 1: 40 frequency-ranked decisions | 165 | 570 | 80.1% |
| Round 2: 10 grouped decisions, 1,004 names | 502 | 1,265 | 84.5% |
| Prose stripping, and separating apparatus notes/bare units from reagents (parser fixes) | 502 | 1,265 | 85.2% (87.5% on chemistry alone) |
| Ionic liquids added (v0.4.0); aliases naming a different molecule removed (v0.5.x) | 542 | 1,306 | 85.3% |
| Two gold sets sampled and labelled; isomers the teacher's false positives exposed (v0.6.x) | 562 | 1,370 | 85.3% |
| De-duplication: PEG MME's seven ids per weight merged to one, all remaining duplicate ids merged, method text and element abbreviations stripped out (v0.7.0–v0.8.7) | 486 | 1,487 | 85.7% (88.3% on chemistry) |
| v0.9.0: the Morpheus stock table, 15 premixes transcribed from the vendor brochure | 499 | 1,543 | 85.9% (88.4% on chemistry) |
| v0.9.2 (current): every pre-merge canonical id resolves again | **499** | **1,583** | **86.5%** (89.0% on chemistry) |

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

Two rounds of curation and one parser fix took identification from 79.1% to 85.2%. The marginal return has since fallen to roughly 15 components per decision, so further curation rounds are no longer the best use of expert time; the remaining gains have come from the residual parser rather than the dictionary.

## 🎓 Training

**In plain terms:** the rule-based parser reads most of the archive, but some depositions are written in prose it cannot follow. A small language model was trained to read those, by learning from the hundreds of thousands of examples the rule parser already handles correctly. It costs nothing in hand-labelling, because the rule parser writes its own teaching material.

**For the crystallographer:** SmolLM2-360M under MLX-LM LoRA on an M1 Max, prompt masked so the loss falls on the answer rather than on echoing the question, evaluated on the residual the rule parser could not read.

- **Validation loss is the wrong stopping signal.** The labels are the rule parser's own output, so validation loss measures fidelity to the teacher rather than skill on the residual: it flattens early while identification is still climbing. Checkpoints are chosen by sweeping downstream accuracy on the residual instead.
- **Longer training clearly helps recovery, and that is part of the improvement story.** On the 2,000-record frozen benchmark, round 09's identification rises 5.5 points and fully-identified records rise 15 points between true iteration 1,000 and true iteration 8,000, and the number of distinct names the model can't identify more than halves. Round 09 is a better recipe *and* a longer one than round 06 (8,000 iterations, ~1.04 epochs, against round 06's 6,000, ~0.94 epochs).
- **Small evaluation sets can hide a real difference.** The 192-record gold sets showed almost no gap between round 09's early and late checkpoints; the 2,000-record frozen benchmark resolved the same comparison easily. Where a metric is reported below, its sample size is given for exactly this reason.
- **Gates are judged on the lower confidence bound**, so a lucky sample cannot pass them.

Training data is deduplicated before oversampling: see [Redundancy](#-redundancy) for why that is not a detail. Every run is named `r1-parse-residual-smollm2-360m-roundNN` and logged to Weights & Biases, with the adapter directory carrying the same name so a checkpoint on disk traces back to the run that produced it.

## 📐 How the model is measured

**In plain terms:** it is easy to build a metric that a bad model passes. If you only ask "did it output a real chemical name", a model that answers *sodium chloride* to every question scores well, because sodium chloride is a real chemical. The measures below were chosen so that answering confidently and wrongly is penalised, not rewarded.

| Measure | Asks | Blind to |
|---|---|---|
| **Schema validity** | Is the output parseable JSON using only the allowed roles and units? | Whether any of it is true |
| **Identification** | Does each named reagent exist in the curated lexicon? | Whether it is the *right* reagent for this text |
| **Grounding** | Is each named reagent actually mentioned in the text it was given? | Whether the amount and unit are right |
| **Fidelity** | On text the rules *can* read, does the model produce the same components? | The residual, which is by definition harder |

None of those four has any ground truth behind it: they ask whether an answer is *defensible*, not whether it is *right*. Precision and recall, defined below, are computed against hand-labelled truth and are what decide anything.

**Identification is the weakest of the four.** A model reading `20% PEG 3350` and emitting `SODIUM_CHLORIDE` scores as identified: the name is real, it is simply not the reagent in front of it. Since the whole purpose of curation is to make names mean something, a measure that cannot tell a real name from the correct one is not measuring the thing that matters.

**Grounding closes that hole and needs no labels.** If the model names a reagent, some spelling of that reagent should appear in the source string. Punctuation and spacing are stripped on both sides, so `peg-3350`, `PEG3350` and `peg 3,350` all match one alias. A failure is either a hallucination or a spelling the lexicon has never seen, and both are worth knowing about, so ungrounded cases are written out for inspection rather than only counted.

**Every one of those four is precision-shaped.** Each asks whether what the parser *said* is defensible; none can see a reagent it never mentioned at all, so a parser that reads one component per record and stops scores well on all four. Measured directly: on 4,000 residual records, 67% contain more reagent clauses than the parser emitted components. That blind spot is exactly what the gold sets and the frozen benchmark below exist to close.

## 🏅 Results against hand-labelled truth

Everything else in this project checks whether an answer is defensible. This section checks whether it is *right*, against two kinds of hand-labelled ground truth.

**Two gold-set batches, always pooled.** A random 96 records answers "how good is the pipeline overall", but rarely contains a reagent the sources disagree on, so it under-tests precision. A second 96, sampled specifically where the teacher and the rule parser disagree, supplies that. Both are labelled by hand in `gold_bench_v1.html`, one reagent list per condition, and both are pooled (192 records, 616 reagents) rather than averaged, so the headline figures below are one number with a real confidence interval. Neither batch is ever trained on, and both are excluded from every teacher run by name.

| source | found | missed | falsely added | precision | recall |
|---|---|---|---|---|---|
| rule parser alone | 428 | 188 | 22 | 95.1% | 69.5% |
| rules + round 06 | 528 | 88 | 26 | 95.3% | 85.7% |
| rules + round 09 (shipped, iteration `SHIPPED_ITER`) | 567 | 49 | 46 | 92.5% | 92.0% |

*Both batches pooled, measured 2026-08-04 at lexicon 0.9.2.*

| Measure | Asks | Fails when |
|---|---|---|
| **Precision** | Of the reagents claimed, how many really are in the condition? | The parser invents chemistry |
| **Recall** | Of the reagents really in the condition, how many did it find? | The parser misses chemistry |

**Round 09 finds 39 more reagents than round 06 and misses 44% fewer.** Its extra false positives are not inventions: every one names a reagent genuinely present in the source text (a protein storage buffer, a soak, a cryoprotectant) placed in the wrong role, which is what the `protein_buffer` and `soak` scope roles exist to fix. In short: **round 09 finds more chemistry and misfiles slightly more of it.**

**For the crystallographer:** the gold sets are a small yardstick with wide intervals. They measure reagent *identity*, not concentration or unit, and they don't replace the commercial screen cross-reference (81,802 component-set matches) or the human audit.

### The frozen benchmark: 2,000 held-out residual records

The gold sets are precise enough to rank sources but, at 192 records, too small to resolve some real differences: see [Training](#-training). A larger, held-out slice of the residual (2,000 records, never used for training or checkpoint selection) is used to compare rounds directly, including across a single round's own checkpoints:

| | round 06 | round 09 @ true 1,000 | round 09 @ true 8,000 |
|---|---|---|---|
| identification | 89.05% | 89.76% | 95.28% |
| grounding | 93.15% | 93.04% | 94.83% |
| fully-identified records | 62.85% | 65.8% | 81.0% |
| fidelity (exact match) | 91.95% | 80.5% | 94.38% |
| distinct unidentified names | 629 | 635 | 294 |

*Measured 2026-08-04 at lexicon 0.9.2. Round 09's shipped checkpoint is `SHIPPED_ITER`, still being finalised within this range.*

## 🤗 Every round, and what it delivered

All rounds ship as LoRA adapters for `mlx-community/SmolLM2-360M-Instruct`, one directory per round in **[`Dellboy/toppdblx-residual-parser`](https://huggingface.co/Dellboy/toppdblx-residual-parser)**. Each is 33 MB; the base model is not redistributed. Rounds that didn't reach the corpus are listed anyway, since the point is the arc.

| Round | What changed | Identification | Grounding | Gold sets: precision / recall | Components shipped |
|---|---|---|---|---|---|
| [01](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round01) | Bootstrap distillation from rule output, lexicon 0.1.0 | 87.0% † | not yet invented | - | N/A ‡ |
| [02](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round02) | `not_a_component` class added, confidence gate fixed | 89.7% † | not yet invented | - | N/A ‡ |
| [03](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round03) | Class rebalanced; cosine decay and dropout introduced | 88.4% † | not yet invented | - | N/A ‡ |
| [04](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round04) | Retrained on the 502-reagent lexicon | abandoned (36% duplicate training rows) | - | - | N/A ‡ |
| [05](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round05) | Deduplicated training set; first round scored on a frozen benchmark | 87.58% | 93.41% | - | N/A ‡ |
| [**06**](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round06) | Full epoch, rank 16, 32 LoRA layers, empty-answer examples added | 89.05% | 93.15% | 95.3% / 85.7% | **153,736** |
| [07](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round07) | 32B-teacher labels *replacing* rules labels, 16 LoRA layers | not measured | not measured | superseded by round 09, see below | N/A ‡ |
| [08](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round08) | Same idea, labels filtered for precision, trained 4x longer, still 16 LoRA layers | not measured | not measured | superseded by round 09, see below | N/A ‡ |
| [**09**](https://huggingface.co/Dellboy/toppdblx-residual-parser/tree/main/round09) | **Shipped 2026-08-04.** Teacher labels *added* to the rules labels, not substituted; `protein_buffer`/`soak` scope roles; prompt v2; 32 LoRA layers, matching round 06 | 95.28% § | 94.83% § | **92.5% / 92.0%** | pending ¶ |

† Rounds 01–03 were scored against a live residual that shrank as curation improved, so these three figures are not comparable to each other or to later rounds. The comparable, frozen-benchmark story starts at round 05.

§ Round 09's final adapter (true iteration 8,000). The shipped checkpoint (iteration `SHIPPED_ITER`) is being finalised on this benchmark; see [the frozen benchmark](#the-frozen-benchmark-2000-held-out-residual-records) above.

‡ N/A rather than 0: the column counts rows that reached the released dataset. Only round 06 has ever been applied to the corpus at scale before round 09; every other adapter was a training experiment, measured and set aside, and was never run over the residual.

¶ Round 09 was chosen 2026-08-04 and the corpus has not yet been regenerated with it, a roughly 4.7-hour pass. **Until it completes, the released dataset still carries round 06's 153,736 components.**

### Round 09: why it ships over round 06

Round 09 is the first clean test of whether teacher distillation helps when the teacher's labels are *added* to the rule parser's labels rather than replacing them, at matched LoRA capacity. It does: pooled recall against hand-labelled truth rises from round 06's 85.7% to 92.0%, finding 39 more reagents and missing 44% fewer, for a rise in role-misattribution errors rather than invented chemistry (see [Results against hand-labelled truth](#-results-against-hand-labelled-truth)).

Three things changed together: every rules label was kept and roughly 3,400 teacher-labelled residual rows were added on top; the run used 32 LoRA layers, matching round 06 (rounds 07 and 08 had trained at 16, since `--num-layers` was left at its default); and the prompt gained `protein_buffer` and `soak` roles so a correctly-identified reagent in the wrong role has somewhere to go. A build-time gate fails the run rather than training on a leak: zero gold records reached training, verified after the fact.

Round 09 also trains for longer than round 06 (8,000 iterations against 6,000), and the frozen benchmark shows that this matters on its own: identification and fully-identified records both keep climbing well past round 06's schedule. Exactly which checkpoint ships is still being finalised; it will be named here as round 09, iteration `SHIPPED_ITER`, once that measurement lands.

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
| **Gold sets** | **96, then another 96** | **Precision and recall against labelled truth, the yardstick for every model round** |

`app/condition_courtroom_v5.html` renders any payload with the same shape, so a new audit is a new generator stage rather than a new page. `condition_courtroom_v7.html` is the accuracy audit: one condition per screen, one checkbox, and a three-field diagnosis that appears only when a card is ticked, so the ~90% waved through stay a single click. `gold_bench_v1.html` is the labeller: deposition text, removable reagent chips, and an autocomplete over all lexicon names for what the pipeline missed.

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
| `eval.gold_questions` | Samples records for hand labelling, banded by text length |
| `eval.gold_metrics` | Precision and recall against the gold sets, per source and pooled |
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

The residual components in this release (153,736 of them) currently come from round 06; round 09's regeneration is running but not yet applied, see [Every round](#-every-round-and-what-it-delivered).

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
| Fidelity to the rules is a ceiling, not evidence of beating it | The rule parser's own labels train the residual model, so agreement with `rules_v3` cannot show a gain; only identification and grounding on the residual, and precision/recall against the gold sets, can |
| The residual parser cannot discover new chemistry | It emits only names it has seen, so a genuinely absent reagent looks the same as a model error. Closing that gap is curation, not modelling |
| The gold sets are 192 records pooled | Wide confidence intervals, reagent identity only, no concentrations. Makes "did we miss something" answerable, not precisely answerable |
| Recall is the weaker half of the pair | Rules alone: 95.1% precision against 69.5% recall, the parser is far likelier to miss a reagent than invent one. Rules + round 09 narrows that gap to 92.5% precision, 92.0% recall |

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
- [x] Remove the aliases naming a *different* molecule: barium/yttrium, CAPS/CHAPS, DTT/DTE and four more
- [x] Transcribe the Morpheus stock table from the vendor brochure: 15 premixes with real compositions
- [x] Classify premixes on their constituents rather than refusing them: coverage 76.8% to 80.2%
- [x] Split reagents written with no separator at all: +7,553 identified components, nothing lost
- [x] **Teacher distillation, resolved:** round 09 adds the 32B's labels to the rules labels instead of replacing them, at 32 LoRA layers matching round 06. Pooled recall against hand-labelled truth rises from 85.7% to 92.0%, finding 39 more reagents; rounds 07 and 08 had changed the label source and halved LoRA capacity in the same experiment
- [x] **Ship round 09, preferring recall over a precision-weighted score:** it finds more chemistry and misfiles slightly more of it, and the extra errors are role misattributions (a real reagent in the wrong scope), not inventions
- [x] Confirm training length matters: on the 2,000-record frozen benchmark, round 09's identification and fully-identified records keep rising from true iteration 1,000 to 8,000; the 192-record gold sets were too small to resolve the same gap clearly
- [ ] **Finalise which round 09 checkpoint ships** (currently marked `SHIPPED_ITER`) and measure it on the frozen benchmark
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
