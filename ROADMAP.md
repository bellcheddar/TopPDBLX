# TopPDBLX: roadmap to completion

**Plan for the remaining work, written 2026-07-30 after Phases 0 and 1.**
**Convention:** British English, no em dashes, one command per stage, manifest per stage.

This is the second planning pass. The first (`PHASE0_PLAN.md`) planned Phase 0 from the brief.
This one plans everything remaining, and it departs from the brief's ordering in one important
way, because two of the brief's premises have now been contradicted by measurement.

---

## 1. Where the project actually is

| | |
|---|---|
| Records | 199,185, of which **183,623 usable (92.2%)** |
| Components | 603,459, **79.1% resolved** to a canonical reagent |
| Reagent lexicon | 147 reagents, 501 names, two rounds of expert audit applied |
| Condition ontology | 163 groups (41 L2, 122 L3), all L3 anchored to an orderable well |
| Group assignment | 44.4% at L3, 26.6% at L2, 29.0% unassigned |
| Sequence linkage | 251,559 entries, 23,868 clusters at 30% identity |
| Archive fidelity | **100.0000%** over 205,943 entries |
| Splits | leak-free at 30%, 50% and 90% simultaneously |
| Released | JSONL, Parquet, CSV, DuckDB, JSON Schema, generated datasheet |
| Tests | 234 |
| Licence | CC-BY-4.0 data, MIT code, public on GitHub |

Phases 0 and 1 are complete. Every open decision in the brief's section 12 is now settled.

## 2. The finding that reshapes the rest of the plan

The brief predicts (7.3): *"Report against a homology retrieval baseline ... This baseline will
be strong. If the learned model does not beat it, that is a cheap and useful negative result."*

**Homology retrieval is not strong. A query-independent frequency prior beats it at every
level, on both split thresholds, under all three definitions of ground truth.**

| 30% split, hit@1, truth = 90% cluster | prior | homology | hybrid |
|---|---|---|---|
| L1 | **37.9%** | 29.6% | 37.7% |
| L2 | **21.1%** | 11.0% | 16.2% |
| L3 | **10.3%** | 5.3% | 8.5% |

This survived every attempt to make it go away: fixing two evaluation flaws that had favoured
the prior, adding a homology-then-prior backoff, multi-label truth across three definitions,
and repairing a split leak that had been *inflating* homology. Removing the leak made homology
worse.

The interpretation is in the brief's own section 11.2: **condition frequency reflects screen
popularity, not protein identity.** Homologues were screened with the same popular screens as
everything else, so there is little sequence-specific signal to retrieve.

**What follows for planning.** The brief's Phase 3 leads with a learned condition recommender.
On this evidence that is the *least* promising of its three components, not the most. The other
two do not depend on the weak condition signal at all:

- **Construct boundaries** (spec 8.2) learn from where crystallographers actually cut, which
  is tens of thousands of labelled examples independent of condition choice.
- **Crystallisation propensity** (spec 9) learns from TargetTrack successes and failures, again
  independent of which condition was used.

So the order is reversed: build the two things with strong evidence first, and attempt the
condition recommender last, with a kill criterion agreed in advance.

---

## 3. Work packages

Effort is working days for one person. Each package states what would make it a failure, so it
can be stopped rather than nursed.

### R1. Parse residual: fine-tune SmolLM2 on what the rules cannot read
**5 days. No dependencies. Highest-value remaining data work.**

125,970 components (20.9%) resolve to no canonical reagent, and 29.0% of records reach no L3
group. Both thin every downstream metric, so this precedes any modelling.

- Target only the residual: records where `rules_v3` leaves uncovered text or disagrees with a
  near screen match.
- Bootstrap labels from high-confidence rule output, hand-correct 3,000 to 5,000 prioritised by
  low confidence, plus a random 500 so the model does not learn that hard cases are typical.
- SmolLM2-360M under MLX-LM LoRA, prompt masked, constrained to schema-valid JSON, W&B logging.
- Run `preflight.sh` before every launch; never disable `grad_checkpoint` to fit memory.
- **The circularity trap:** labels bootstrapped from the rule parser cannot prove the model
  beats the rule parser. The held-out audit set is hand-labelled from raw text and must never
  be used for training or threshold tuning.
- **Failure condition:** component resolution does not exceed 85%, or schema validity falls
  below 99%. Then keep `rules_v3` and record the attempt.

#### R1 progress, 2026-07-30

**Hand-labelling was not needed to start.** The plan above assumed 3,000 to 5,000 hand-corrected
records. Instead the first experiment was pure distillation from the rule parser's own
high-confidence output: 102,417 training pairs and 14,874 validation pairs at **no labelling
cost**, split by the existing leak-free sequence clusters, with the 81,803 records the rules
could not read held back as the target. Hand-labelling is now an escalation, not a prerequisite.

**Finding 1: the resolution denominator was wrong.** Of the 125,970 unresolved components,
**8.1% contain no chemistry at all**: method text (4,499), screen references (3,201), unnamed
proteins and ligands (1,691), bare splitter fragments (727), publication references (88). These
sat in the denominator, so the parser was scored as having failed to resolve text with no reagent
in it. A `not_a_component` role now records the verdict with an auditable reason, moving measured
resolution from **79.13% to 80.49% without changing a single parse**. The 85% gate was previously
set against a partly unreachable target.

The classifier is deliberately conservative and asymmetric: a false positive silently deletes
real chemistry, while a false negative merely shows up in the coverage report. A concentration
vetoes the substring rule (a depositor writing an amount was naming a substance) but not the
anchored whole-clause rules, because "1 mM inhibitor" still names nothing.

**Finding 2: the residual is a long tail, so a model is the right tool.** 45,573 distinct
unresolved strings, of which the top 1,000 cover only 38.5%. Curation cannot close that; only
generalisation can. There is still a seam of cheap lexicon wins that no model can supply
(`methyl-2,4-pentanediol` = MPD, 216; `ca(oac)2`, 200; the SPG/PCTP/PDTP buffer systems, 585
combined), which is a curation round worth running alongside.

**Finding 3: validation loss is the wrong stopping signal, and the useful training is far shorter
than expected.** Val loss flattened at 0.004 by iteration 1,200, but it measures *fidelity to the
rule parser*, because the labels are its output. The metric that decides R1 is residual
resolution, and it behaves differently:

| checkpoint | residual resolution (95% CI) | schema valid | records fully resolved | fidelity exact |
|---|---|---|---|---|
| 100 | 72.78% [71.04, 74.44] | 97.62% | 39.00% | 67.75% |
| 300 | 80.61% [79.00, 82.12] | 98.50% | 49.75% | 78.12% |
| 600 | **87.01% [85.58, 88.32]** | 98.75% | 65.25% | 86.00% |
| 900 | 85.17% [83.67, 86.55] | 99.00% | 62.12% | 87.38% |
| 1300 | 86.80% [85.36, 88.11] | 98.50% | 64.25% | 91.00% |

Resolution climbs steeply to iteration 600 (the first three intervals are disjoint) and then
**plateaus at about 86%**, while fidelity keeps rising to 91%. So the model goes on getting better
at imitating the rule parser long after it has stopped getting better at the residual: the
transferable signal is exhausted by **iteration 600, which is 0.09 of an epoch**. A full epoch is
not worth running.

Two process rules follow, both learned the hard way here:

- **Sweep residual resolution across checkpoints; never stop on validation loss.** Stopping on
  val loss was wrong, and so was extrapolating the climb from three points, which suggested
  extending to a full epoch. The fourth and fifth points showed a plateau.
- **Judge the gates on the lower 95% bound, not the point estimate.** On point estimates 900
  passes the 85% resolution gate at 85.17%; its interval reaches 83.67%, so it has not been shown
  to. Wilson intervals are now computed in `models.eval_slm` and the pass/fail flags use the lower
  bound. An 800-record sample cannot separate 85% from 87%, and reading those point estimates as
  a peak followed by a decline was exactly the error the intervals prevent.

**Finding 4: adding the class was not enough; it had no training signal.** After `not_a_component`
was wired in, the rebuilt training set contained **zero** instances of it, so the model could
never learn the verdict. Two nested causes, both the same denominator mistake as Finding 1:

1. Record confidence was `resolved / n_reagent_clauses`, which counted a method-text clause as a
   reagent clause the parser had failed on. Any record mentioning its own method was capped below
   1.0 (median 0.735, maximum 0.925).
2. Corrected, confidence still peaked at **0.998** rather than 1.0, because stray punctuation
   leaves character coverage fractionally short. The `CONFIDENT = 1.0` gate was an exact-equality
   test on a float that real deposition text never reaches.

The gate is now 0.95, which with resolution pinned at 1.0 by the `accounted_for` check means at
least 83% character coverage. Result: 1,286 training labels for the class, training set 102,417 to
103,655, kept records 183,623 to 183,714, and 351 records moved from `NO_REAGENT_MATCH` to
`METHOD_ONLY`, which is what they always were.

**Finding 5: the head of the tail is curation, not modelling, and the recommender needed
chemistry guards.** `parse.lexicon_questions` ranks the unresolved strings by corpus frequency
and asks 40 questions covering 7,018 components, each standing for every occurrence of that name.
Output feeds the existing payload-driven `condition_courtroom_v5.html`.

The first version was **unsafe**, and would have been worse than asking nothing, because every
dropdown is pre-selected and there is an accept-all button. Unguarded string similarity proposed
`peg 3400` → PEG_400 (an eightfold molecular weight error), `peg 3500` → PEG_300,
`1,3-propanediol` → PROPANEDIOL_12 (the wrong isomer, the same mistake as the 1,4-butanediol one
caught in the Phase 0 audit), `strontium chloride` → SODIUM_CHLORIDE, and both `sodium maleate`
and `malonate` → MALIC_ACID. Three guards now apply:

- **numbers must be identical**, because a number in a reagent name is molecular weight or
  substitution position, never decoration;
- **element and acid words must agree**, so strontium does not match sodium and maleate does not
  match malate;
- **family names resolve to nothing.** `peg` (720 components), `phosphate` (358), `propanediol`,
  `butanediol`, `citrate buffer` state a family without a member. Recommending a specific entry
  would invent data the depositor never gave, so these are marked ambiguous.

Guards govern what is *recommended*, never what is *available*: rejected candidates still appear
in the dropdown labelled "NOT recommended: numbers differ ...", because `methyl-2,4-pentanediol`
really is MPD and hiding it would put the correct answer out of reach.

Configuration that worked: `mlx-community/SmolLM2-360M-Instruct` (no precision suffix exists;
HuggingFace answers 401 for a missing repo, so a typo reports as an authentication failure), all
32 layers, LoRA rank 8, batch 16, lr 1e-4, `--mask-prompt`, `--grad-checkpoint`, peak memory
4.85 GB.

Two machine hazards, both now in the standing notes: `HF_TOKEN` is stale and must be stripped
from any subprocess touching the hub; and writing 17 MB checkpoints into iCloud-synced
`~/Documents` every 100 iterations drives `fileproviderd` to 150% CPU, starving the GPU work
being measured. Checkpoint staging now happens outside the synced tree.

### R2. Publish the dataset
**2 days. Depends on R1 only if you want the improved parse in v1.0.0.**

- Zenodo deposit for the citable DOI; HuggingFace Datasets under `Dellboy` as the working mirror.
- Bump `DATASET_VERSION` to `1.0.0` and freeze `SCHEMA_VERSION` at `1.0.0`.
- Put the DOI into the datasheet citation and the README.
- **Decision needed:** publish now at 0.1.0, or after R1 at 1.0.0. Publishing now gets a DOI
  people can cite while the model work proceeds; waiting means one clean release.

### R3. Construct boundary model
**8 days. Depends on residue-level SIFTS, not yet fetched.**

The strongest remaining signal, and it needs no condition data.

- Fetch residue-level SIFTS XML (segment-level is already in hand and is not sufficient here).
- Align every SEQRES against its UniProt full-length sequence: 53,431 references already
  fetched. This yields tens of thousands of examples of where crystallographers actually cut,
  at no labelling cost.
- Train ESM-2 t12-35M as a token classifier: full-length sequence in, per-residue probability
  of being inside a crystallised construct out.
- Propose boundaries by thresholding and smoothing, subject to minimum construct length, not
  cutting mid-helix, and not cutting inside a predicted domain.
- Report per-residue MCC and boundary distance in residues against held-out real constructs,
  split by the existing leak-free clusters.
- **Failure condition:** per-residue MCC below 0.4, or median boundary error worse than 20
  residues. Both would mean the boundary prior is not learnable from sequence at this scale.

### R4. Crystallisation propensity model
**6 days. Depends on TargetTrack, already acquired and checksummed.**

The only part of the project that can address P(crystallised | sequence) rather than
P(condition | crystallised), because it is the only part with negative examples.

- Process the TargetTrack archive (833 MB, already on disk with its MD5 verified).
- **Open decision:** which status transition counts as a positive. The brief lists crystallised,
  diffracted and deposited. This must be fixed before training and stated in any result.
- Features: XtalPred-style physicochemical properties, disorder from metapredict, coiled-coil,
  low complexity, plus frozen ESM-2 embeddings.
- Gradient boosting, not a neural net, at this data volume.
- Report AUC and precision-recall on held-out targets, with the positive definition stated.
- **Known limitation to publish, not hide:** TargetTrack stops in the mid 2010s, so the negative
  set is era-limited and skewed towards structural genomics targets.
- **Failure condition:** AUC below 0.65 on cluster-split held-out targets, which would not beat
  published predictors and should be reported as such.

### R5. Condition recommender, with a kill criterion
**4 days. Depends on R1. Attempt last, on the evidence in section 2.**

- Frozen ESM-2 embeddings plus a shallow **multi-label** head. Multi-label is not optional:
  proteins crystallise in several unrelated conditions, and decision 12.4 is settled at the 90%
  cluster.
- Evaluate with the existing harness: same leak-free splits, same baselines, hit@k at L1, L2, L3.
- **Kill criterion, agreed before starting:** if it does not beat the frequency prior at L2
  hit@5 by at least 3 percentage points, stop and publish the negative result. Do not tune it
  into looking good.
- The negative result is publishable and useful either way: it would say the PDB supports
  choosing *screens* by precedent but not by sequence.

### R6. Browser front end
**4 days. Depends on R1; independent of R3 to R5.**

Single-file HTML, vanilla JavaScript, Plotly for charts, Tabulator for tables, marcdeller.com
theme, mobile responsive, no build step.

Framed as an **exploration tool, not a predictor**, which is what the evidence supports:

- Paste a sequence, see homologous PDB entries and the conditions that crystallised them.
- Show the condition group, its screen anchor, and the confidence band.
- Show the frequency prior alongside, honestly labelled, so a user can see when precedent adds
  nothing over "what everyone tries".
- Deploy to `toppdblx.mdeller.com`, add to the mdeller.com launcher via `apps.json`.

### R7. Dataset paper
**6 days. Depends on R1 to R5 completing or being explicitly abandoned.**

Phase 0 was always the value proposition and it stands alone. The paper writes itself from the
datasheet plus the findings, several of which contradict prior expectation and are the
interesting part:

- 92.2% parse coverage where a rule parser was expected to plateau at 75%, because the
  difficulty was clause splitting rather than chemistry.
- 100.0000% archive fidelity over 205,943 entries.
- Roughly a fifth of usable records name a precipitant but never state how much.
- A frequency prior beats homology retrieval for condition prediction.
- The discard distribution, and the fact that "see publication" is negligible at 107 records.

---

## 4. Order, and what it depends on

```
R1 parse residual ─┬─▶ R2 publish (or publish now at 0.1.0)
                   ├─▶ R5 condition recommender (kill criterion) ─┐
                   └─▶ R6 front end ───────────────────────────────┤
R3 construct boundaries (needs residue-level SIFTS) ──────────────┼─▶ R7 paper
R4 propensity (needs TargetTrack processing + a positive definition)┘
```

R3 and R4 are independent of everything else and of each other, so they can run in any order or
in parallel with R1.

| Package | Days | Evidence it will work |
|---------|------|----------------------|
| R1 parse residual | 5 | Strong: the residual is concentrated and the rules already reach 92% |
| R2 publish | 2 | Certain: the artefacts exist |
| R3 construct boundaries | 8 | Strong: tens of thousands of free labels, independent of condition signal |
| R4 propensity | 6 | Moderate: the only negative examples available, but era-limited |
| R5 condition recommender | 4 | **Weak: a query-independent prior already beats retrieval** |
| R6 front end | 4 | Certain as an exploration tool; not as a predictor |
| R7 paper | 6 | Strong: Phase 0 stands alone as a resource |
| **Total** | **35** | about seven working weeks |

## 5. Decisions still needed

All four were settled on 2026-07-30, as recommended.

| # | Decision | Settled |
|---|----------|---------|
| A | Publish at 0.1.0 now, or wait for 1.0.0 | **Publish now at 0.1.0.** A citable DOI while modelling proceeds; R1 lands as 1.1.0 |
| B | Which TargetTrack status counts as a positive | **Crystallised**, since that is what this database predicts. Diffracted reported as a secondary target |
| C | Accept the R5 kill criterion | **Yes.** If the recommender does not beat the frequency prior at L2 hit@5 by three points, stop and publish the negative result |
| D | Refine the machine-generated group labels | **Yes, the 41 L2 labels only.** The 122 L3 labels stay generated |

## 6. What I recommend not building

- **A learned model to fill the 29% unassigned at L3.** Those records mostly state no
  concentration at all. A model would be inventing numbers, and the honest answer is that a
  concentration-based ontology cannot place them.
- **Emergent clustering as the ontology.** Already rejected in Phase 1 and confirmed by the
  diagnostic: 1,118 L3 cells would be needed for 90% coverage, and they would not be orderable.
- **Anything that requires the 90 GB archive snapshot to stay on disk.** Its agreement report is
  the artefact; the bytes are reclaimable at any time.
