# What to do next

Written 2026-07-31 so that nothing outstanding lives only in a conversation. Everything here is
either running, generated and waiting for an answer, or specified and not yet built.

## 2026-08-03, 21:25: the teacher run had to be killed and resumed

**It degraded 16x under memory pressure and would not have finished.** Batches went 82 s → 192 →
245 → **1,332 s**, and the ETA reached 20 hours with 86 batches left. Free memory had collapsed
from 4.8 GB to **1.05 GB**, with 1.4 GB in the compressor and swapins climbing. No thermal
warning; this is memory, not heat.

This is the failure `models/teacher_label.py` documents — macOS paging the model's buffers out
under sustained load — but worse than the 220–577 s/batch it records. The 32 GB wired limit was
set and held; what ran out was everything else.

**The recovery, which worked and is worth repeating:**

1. Stop the waiting chain *first*, so it cannot act on a half-written state.
2. `kill` the teacher — **not** `kill -9`. The progress file is appended per batch, so a clean
   stop loses nothing: 2,168 of 2,779 records were already saved.
3. Free memory: `bash scripts/preflight.sh`, plus stopping the third-party monitors that had been
   accumulating. Free pages went 67,219 → 3,128,307, about **1 GB → 48 GB**.
4. Relaunch with the identical `--records`, `--progress` and `--out`. `teacher_label` reads the
   progress file and resumes: *"2,779 target records, 2,168 already done, 611 to label"*.

**Back to 115 s/batch**, against 82 before the degradation and 1,332 at its worst. 77 batches
remain, so about 2.5 hours. Free memory holding at 14.3 GB.

**What to watch.** 1.25 GB of swap is still in use and preflight recommends a reboot for a clean
slate, which was not practical mid-run. If an 8-hour training run degrades the same way, the same
recovery applies — `train_slm` also resumes, though note that resuming restarts the iteration
counter and the LR schedule, so the reported iteration is not the true one.

## 2026-08-03, 18:40: tonight is chained in `TONIGHT.sh`

Running unattended. Log at `data/interim/slm/tonight.log`.

1. **Wait for the teacher.** Polls for `teacher_components_blank.parquet`, which `teacher_label`
   writes once at the end — *not* `until ! pgrep -f`, which matches its own command line and never
   exits. Six-hour deadline.
2. **Score rounds 06, 07 and 08** on the frozen benchmark at `--limit 500`, against tonight's
   lexicon, all with prompt v1. ~7 minutes each. This is what makes the identification column
   comparable: round 06's published 88.99% was measured against 502 reagents and today's lexicon
   has 499 with 92 ids retired, so the old number and a new one are different measurements.
3. **`RUN_ROUND09.sh`** — re-parse, rebuild, gold-leak gate, token-length gate, preflight, train
   at `--num-layers 32`.

**Timing.** The teacher's ETA drifted while this was being set up: it showed 45 minutes at 17:50
and 3 hours at 18:40, because the average was dragged by a stall earlier in the day. Expect the
teacher ~21:45, evals to ~22:10, training finishing **~06:10**.

**In the morning:** `SCORE_ROUND09.sh` for round 09's identification and grounding, the checkpoint
sweep on both gold sets, and — once a checkpoint wins — the components. Round 06 stays shipped
unless round 09 wins on F0.5.

**Components shipped stays N/A for every round but 06**, deliberately. That column counts rows
that reached the released dataset and only round 06 was ever applied to the corpus. Generating a
real number for rounds 07 and 08 costs 4.7 hours of generation each, measured from round 06's own
282-minute manifest, and answers a different question than the column asks.

## 2026-08-03: scoring round 09 — `SCORE_ROUND09.sh`

Run it after training. It produces every column the round table needs, and two of them need care.

**Identification and grounding are scored for round 06 as well, today, not quoted from July.** The
frozen benchmark fixes the *records* (2,000 of them, header says `lexicon_version: 0.3.0,
n_reagents: 502`) but identification asks whether an emitted name is **in the lexicon**, and the
lexicon has gone 502 → 499 reagents through today's merges with 92 ids retired. Round 06's
published 88.99% and a round 09 figure measured now are therefore not the same measurement.
Scoring both against the same dictionary is the only way the column means anything.

**Each round is served with the prompt it was trained under** — round 06 with `--system-version
v1`, round 09 with `v2`. A mismatch does not fail, it answers slightly worse and reports nothing.

**A new progress file for round 09.** `apply_slm` resumes from whatever `--progress` it is given,
and the default `apply_progress.jsonl` holds round 06's generations — pointing at it would
silently re-ship round 06 under round 09's name. The script uses `apply_progress_r09.jsonl`.

The checkpoint sweep scores on **both** gold sets and picks on F0.5, never on `identification`,
which has given three wrong answers to "how long should this train". Round 06 stays shipped unless
round 09 wins; its current figures are 98.2% / 95.2%, F0.5 97.6.

242 of the 2,000 frozen records have since left the residual because the rules learned to read
them. That is harmless: the frozen file carries its own `text`, so the population is still fixed.

## 2026-08-03: end-to-end review before the final training round

A full-pipeline review. Four findings acted on, three deferred with reasons, and a set of stages
confirmed clean — recorded here because "we checked and it was fine" is worth as much as a bug.

### Fixed

**A teacher parquet is a snapshot of a lexicon, not just of a model.** `teacher_components_2k`
was written on 2 August and names 14 canonical ids that today's merges removed — `PEG_MME_2K`,
`PEG_5K_MME`, `COBALT_HEXAMINE`, `HGCL2` and ten more, across 17 rows. Round 09 would have been
trained to emit reagent ids the pipeline no longer recognises. Regenerating the file needs the
32B and would have contended with the running job; re-resolving at dataset-build time is cheaper
**and better**, because twelve of the fourteen are now aliases of the entry they were merged into
and recover to the right answer rather than being dropped. `_group_components` now canonicalises
against the live lexicon: **15 re-canonicalised, 2 dropped as unplaceable.**

**The dedup key disagreed with the gold key about what "the same text" means.** Gold exclusion
normalised with `" ".join(text.split()).lower()`; the train-set dedup twenty lines later keyed on
the raw string, so **3,114 rows differing only in case or whitespace survived** and were then
oversampled as independent examples. All four keys now normalise identically. Training set
123,521 → **120,789** rows: smaller and cleaner.

**A token-length gate in `RUN_ROUND09.sh`.** `train_slm.py` sized `max_seq_length` from
*characters* ("p99 about 1,050"); the real p99 is 2,197 chars and the true longest example is
**993 tokens against a 1024 cap** — safe, but 3% of headroom, and tonight's rebuild adds rows
that measurement never saw. The gate tokenises the longest examples with the real tokenizer and
**aborts rather than training**, because silent truncation teaches unterminated JSON.

**Three aliases, each verified against the deposition text**: `tris(hydroxymethyl)aminomethane`
(46 records, the literal IUPAC name for Tris), `ddt` (70 records, a DTT typo — DDT the pesticide
does not appear at 5 mM in a drop), `mega8-solution` (38 records).

### Deferred, and why

- **Missing-punctuation clause splitting, ≥207 records.** `"11-13% PEG 8000 5-7.5% GLYCEROL 1 MM
  CUCL2 100 MM SODIUM CACODYLATE"` has no commas at all and becomes one `unknown` blob; 149
  records share that shape, and 58 more have two reagents adjacent with no separator. All the
  chemistry is quantified, so this is the **highest-value remaining parser gap**. Deferred because
  the last splitter change of this kind cost 12,449 spurious components before it was gated, and
  that needs a full-corpus before/after diff rather than a same-day patch.
- **More narrative in `role=unknown`**, 180 records across four phrasings. Same
  pattern-widening risk as above; today already moved 2,416 components this way and the marginal
  set is smaller and less clear-cut.
- **`no_amount` at 26,569 (14.2%)** is now the largest Unclassified reason and is **not a bug**:
  `classify.py` argues it explicitly. Left alone.

### Confirmed clean

Round 09's hyperparameters match round 06's shipped manifest exactly (32 layers, rank 16, dropout
0.05, batch 16, 1e-4 cosine to 1e-5, warmup 50) — no second silent default beside `--num-layers`.
`steps_per_eval`/`steps_per_report` at 50/10 is enforced in code, not just convention. Three
`except Exception` blocks in all of `src/`, each annotated and benign; no bare excepts, no
TODO/FIXME markers. `ingest/`, `link/` and `release/` had nothing.

## 2026-08-03: round 09 plan, and a confound that invalidates how 07 and 08 were read

### Rounds 07 and 08 changed two variables at once

| round | LoRA layers | rank | iters | labels |
|---|---|---|---|---|
| 05 | **32** | 8 | 1,000 | rules |
| 06 *(shipped)* | **32** | 16 | 6,000 | rules |
| 07 | **16** | 16 | 2,000 | teacher |
| 08 | **16** | 16 | 8,000 | teacher |

The base model has 32 layers. Rounds 07 and 08 adapted **half of it**, because
`DEFAULT_LORA_LAYERS` is 16 and neither run passed `--num-layers`; rounds 05 and 06 passed 32
explicitly. So the two teacher rounds cut the adaptable capacity in half *and* swapped the label
source, in the same experiment.

**Every write-up attributes their regression purely to label quality** — this file, the README and
the published model card all say so, and the "consistent trade of about +2 recall for −4
precision" is stated as holding "across a 5-point range of label quality and a 4x range of
training". Neither controlled for depth. **The teacher-label hypothesis has never been tested at
matched capacity**, and the conclusion that distillation is closed does not follow from these two
runs. A silent default did this, not a decision.

### Round 09: what is already prepared

Everything below is built and committed; only the launch waits on the GPU.

- **Prompt v2.** `build_slm_dataset.SYSTEM_V2` adds `protein_buffer` and `soak`. `SYSTEM` (v1)
  is untouched, because it is the contract the public round 06 adapter is served under.
  `apply_slm` and `eval_slm` both take `--system-version`, defaulting to v1. **A round trained on
  v2 must be served with v2**; the mismatch does not fail, it quietly answers worse.
- **Training data rebuilt on v2**: 114,514 rows (was 102,340), all on the new prompt, carrying
  24,040 `protein_buffer` and 16,272 `soak` targets after oversampling — 10.5% of components.
  Verified to contain **zero** of the 29 deleted junk reagents as targets.
- **`--oversample-scope`, default 8**, matching the existing `--oversample-non-component`. Without
  it the rules find scope roles in 1,584 usable records against 114,514, so the model would meet
  the distinction in under 3% of examples and answer with the majority label instead.

### Why this round could differ from 07 and 08

Round 08's false positives were **entirely scope errors** — the model card's own words: "None of
round 08's false positives names a reagent absent from the source text. Every one is a reagent
genuinely present but not part of the crystallisation condition." That is the exact failure the
scope roles now give the model a way to express, and it could not before: every option available
to it was wrong.

### The recipe

Round 06's, at its own capacity, with the fixed data:

    --num-layers 32          matching rounds 05 and 06, not the default 16
    --lora-rank 16 --lora-dropout 0.05
    --batch-size 16 --max-seq-length 1024 --mask-prompt
    --learning-rate 1e-4 cosine to 1e-5, --warmup 50
    --steps-per-report 10 --steps-per-eval 50      (a whole multiple, never equal)
    --run-name r1-parse-residual-smollm2-360m-round09   adapter dir the same basename
    wandb on

6,000 iterations is 0.84 epochs on the new data and took 5.9 h at 32 layers for round 06;
8,000 is 1.1 epochs and about 7.9 h. Checkpoint every 500 and **sweep on the gold set, never on
`identification`** — three answers to "how long to train" have come from `identification` and all
three were wrong.

**Score on both gold sets, 192 records.** The random 96 says how good it is; the contested 96 says
whether it beats the alternatives. Round 06 stays shipped unless round 09 wins on F0.5.

## 2026-08-03: the lexicon change moved every published gold figure

`apply_slm` caches its generations, so re-deriving components under lexicon 0.7.0 cost no GPU at
all — and it exposed a second, older bug beside it.

**`apply_slm` matched canonical ids exactly while `teacher_label` resolved aliases.** The student
emits `PVP`, `TDP`, `MERCAPTOETHANOL`, `COH18N6` — every one a reagent the lexicon knows under
another name — and 658 correct readings were being thrown away for spelling. `teacher_label` has
carried the argument against exactly this since it was written; this stage never got it. The two
now share an identical `canonicalise`, which also matters because a name one stage accepts and the
other refuses would make teacher and student incomparable on the measurement that decides between
them.

    model components   146,324 -> 147,799   (+1,475)
      from lexicon 0.7.0 entries          +1,351 gained, -161 lost to the deleted fakes
      from alias resolution                 +661
    classified                              77.0% (unchanged)
    unidentified_reagent                      761 -> 749

**Both gold sets re-scored, and every headline number moved.**

| random 96 | precision | recall | F1 | F0.5 |
|---|---|---|---|---|
| rules only | 99.5% (was 100.0) | 72.8% (was 67.7) | 84.1 | 92.7 |
| rules + student *(shipped)* | 99.3% (was 99.6) | **92.5% (was 87.4)** | 95.8 | **97.8 (was 96.9)** |

| contested 96 | precision | recall | F1 | F0.5 |
|---|---|---|---|---|
| rules only | 92.0% | 56.5% | 70.0 | 81.7 |
| rules + student *(shipped)* | 92.7% | 74.1% | 82.3 | 88.2 |
| rules + 32B teacher | 91.2% | 86.7% | 88.9 | 90.3 |
| union of both | 91.3% | **97.2%** | **94.2** | 92.4 |
| rules + only where both agree | **92.8%** | 63.6% | 75.5 | 85.0 |

**+5.1 points of student recall on the random batch for a lexicon edit and a fifteen-line
change.** Rules precision drops from a reported 100.0% to 99.5% because a reagent that now
resolves can now also be wrong — the earlier figure was partly an artefact of names that failed
to resolve never being scored at all.

**The six unresolvable gold labels did not move**, and remain the ambiguous abbreviations already
documented: `L-RHA`, `PEA`, `AVA`, `MG(II)`, and two one-off ligands. They are not lexicon gaps.

**The round 06/07/08 and Gemma comparison tables were deliberately left at lexicon 0.6.x** and now
carry a note saying so. Re-scoring round 06 alone would make it beat rounds 07 and 08 on a change
none of them received; the comparison is only meaningful held at one version.

**Anything quoting a gold figure from before 2026-08-03 is stale**, including the HuggingFace
model card, which has not been updated for this and should be.

## 2026-08-03: scope roles, and two claims in this file that were wrong

### Running now

`models.teacher_label` on **2,779 residual records where the rules and the student between them
identified nothing** — total recall loss rather than partial, and the subset where the teacher's
measured recall advantage is worth most. Both gold sets held out (13 records). Steady at ~84
s/batch of 8, so **about 8 hours**, not the 31 estimated from 40 s/record: these texts are short,
median 76 characters. Output `data/interim/teacher_components_blank.parquet`, log
`data/interim/slm/teacher_blank.log`.

**The union ensemble is not deployable over the whole residual and this file implied it was.** At
the observed rate the full 52,145 records is 24 days on this machine. The 96.0% union recall is a
measurement of what is recoverable, not a pipeline. Targeting the blank records is what makes it
affordable.

### The role question, measured rather than assumed

The 32B teacher's false positives across both gold sets, 47 of them, read against their own
deposition text:

| cause | n | share |
|---|---|---|
| **scope** — protein storage buffer, soak, or cryo step | 26 | 55% |
| gold labeller disagreement (reagent is plainly in the text) | ~10 | 21% |
| titrant of a buffer pair read as a component | 3 | 6% |
| wrong isomer or variant | 3 | 6% |
| other | ~5 | 11% |

**This file said "every surviving error is a role problem" and "the schema already has (`role`)".
Both are wrong.** Scope is 55%, not everything. And the schema had `cryo` and `protein` — where
`protein` means *the reagent is a protein* — with no way to say *this is real chemistry belonging
to a step that is not crystallisation*. There was nowhere to put a protein storage buffer, so the
only options were to emit it as a component, which is a false positive, or drop it, which throws
away a correct reading and teaches the model that text it read properly was wrong.

**Built:** `protein_buffer` and `soak` added to `parse.schema.Role`, with
`schema.OUT_OF_SCOPE_ROLES` as the single list everything downstream filters on. `classify` now
skips them on the same argument as the explicit cryoprotectant, and deliberately *without* an
evidence qualifier: `cryo` is hedged because it is mostly inferred from a reagent's identity,
whereas these two are only ever assigned from the depositor's own framing. The teacher's prompt
gains both roles, the cues that signal them, and one worked example.

**Not yet measured.** The prompt change needs a teacher run over the gold records to score, and
the GPU is busy until the blank-record job finishes. Expect it to recover part of 26 FPs of 520
predictions — a ceiling of about 5 points of teacher precision, 91.0% to ~96%, if every scope
error were caught and nothing else broke. Treat that as the optimistic bound, not a forecast.

**The running job predates the prompt change** and will label those 2,779 records without scope
roles. 236 of them (8.5%) carry protein-buffer or soak framing. Re-run *only those 236* under the
new prompt afterwards — 30 batches, well under an hour — rather than redoing eight hours.

### Do not edit `build_slm_dataset.SYSTEM`

It is imported by `apply_slm` and `eval_slm`, so it is both the prompt round 06 was trained under
and the prompt it is served with, for a model that is public on HuggingFace and cannot be
retrained to match. Editing it would re-prompt a shipped model and fail silently — it would still
answer, slightly worse, with no test going red. The scope roles were added to the teacher's prompt
and not to this one. A student round trained on teacher labels needs its own versioned prompt
beside it.

### Lexicon 0.6.2, and the two edits the evidence rejected

`HEXANEDIOL_25` gains four spacing variants and `BUTANOL_2` is new. Two further changes were
queued and then dropped after reading the text: the bare `butanol` alias on `TERT_BUTANOL` looks
exactly like the 0.5.x isomer bugs, but all 25 residual occurrences are `TERTIARY BUTANOL` with
the qualifier split off, so removing it would have cost correct readings; and 639 apparent
`2,4-pentanediol` hits are all `2-methyl-2,4-pentanediol`, which is MPD. **Checking the text
changed two of four decisions.**

### Scope spans in the rule parser — built, measured, not yet applied to the data

The teacher's scope errors turned out to be the *rules'* scope errors too, and the rules run over
the whole corpus rather than the residual. `parse.scope` finds passages that describe the protein
sample or a soak rather than the drop, and `rules` gives components inside them `protein_buffer`
or `soak`.

| | |
|---|---|
| records with a scope span | 5,695 |
| records whose parse changes | 2,727 |
| components re-scoped | 6,759 — 4,143 `protein_buffer`, 2,616 `soak` |
| **pH corrected to the condition's own value** | **343** |
| pH withdrawn as the storage buffer's | 563 |
| **control records (no span) that change** | **0 of 5,000** |

The head of the re-scoped list is a storage buffer and nothing like a screen — sodium chloride
806, Tris 546, DTT 399, sodium azide 195, TCEP 147. Sodium azide is the tell: it is a
preservative, so its presence in a crystallisation condition is nearly a contradiction.

**Design decisions worth not relitigating.** The reagent is kept and only its role changes, so no
correct reading is thrown away. A span needs *both* a protein marker and a following condition
marker — a deposition that is only a protein buffer is left alone, because with nothing to
contrast against the literal reading is the safe one. The role is applied only to components that
**identified**, so an unrecognised reagent inside a span stays `unknown` and remains in the
identification denominator: otherwise the corpus would score better for parsing worse. Only
protein sections withdraw a pH, never soaks, because a soak span runs to end-of-text and swallows
the `pH 5.5, VAPOR DIFFUSION, temperature 293K` block the deposition system appends.

**Two bugs found by looking at withdrawn pH values rather than at the totals.** `Cursor.locate`
searched the original text with clauses the splitter had already lowercased, so on an upper-case
deposition every lookup failed silently and fell back to a stale offset — on 2WWJ that put the
*reservoir's* citrate inside the protein span and discarded the condition's real pH of 5.5.
Nothing raised; the count merely looked plausible. And the first version withdrew pH from soak
spans too, which cost 2IH1 a correct value. Both are now covered by tests.

**Applied 2026-08-03**, together with the titrant guard and lexicon 0.7.0. `parse.run_parser`
takes 62 seconds, so the CPU contention this was deferred for turned out not to matter.

| | before | after |
|---|---|---|
| components identified | 85.3% | **85.6%** (88.0% excluding text with no chemistry) |
| `protein_buffer` / `soak` components | 0 | 4,170 / 2,630 |
| pH from a buffer | 98,835 | 98,585 |
| pH unstated | 87,376 | 87,614 |
| classified | 77.0% | 77.0% |

533 records changed class. `unidentified_reagent` rose 630 → 761 as the 29 fake lexicon entries
stopped resolving, and `no_precipitant` 9,632 → 9,751 as records lost an out-of-scope component
that had been their only one. Both are the corpus becoming honest rather than worse. Identified
components rose *despite* deleting 29 entries, because the 42 real additions more than covered
them. Baselines kept at `*.pre070.parquet`.

### The titrant guard — done

`0.1 M CAPS/ Sodium hydroxide pH 10.5` is one buffer titrated with one base. The corpus writes it
two ways and they failed differently: with spaces the splitter separates them and the titrant
enters the dataset as an additive nobody added; without spaces the clause never splits, so the
whole thing fails to identify and **the buffer is lost too**, which is the worse of the two. Both
are fixed, and the pH is re-attributed to the buffer rather than discarded with the titrant.

Guarded on two tests, both load-bearing: a preceding slash, and *no amount of its own*. 8H28
writes both patterns in one deposition — `400 mM sodium phosphate monobasic / 1600 mM potassium
phosphate dibasic` is two real reagents — and the amount is what separates them. 32 of the 189
records that emitted a titrant match; the other 157 state a concentration and are untouched.

### The titrant pattern, now seen in three places

`CAPS/ Sodium hydroxide`, `BICINE/Tris base`, `MOPS/HEPES-Na` — the second half is the titrant of
a buffer system, not a component. 1,065 residual records match the pattern, though only 266
components corpus-wide currently resolve to a titrant, because aliases already fold the common
`tris/hcl` into `TRIS`. This is the same bug already logged against the Rigaku screen extraction
and visible in the head of the dropped-name list. **Not fixed** — one guard would serve all three
callers, and it needs care, since acetic acid and sodium hydroxide are occasionally real
components in their own right.

## 2026-08-03: the contested gold batch changes the reading

96 records sampled where teacher and pipeline disagree, three equal strata. **32 rejections against
the random batch's 1** — the precision signal the first set could not supply.

| source | precision | recall | F1 | F0.5 |
|---|---|---|---|---|
| rules only | 91.8% | 54.9% | 68.7 | 80.9 |
| rules + student *(shipped)* | 92.5% | 72.8% | 81.5 | 87.8 |
| rules + 32B teacher | 91.1% | **85.2%** | 88.0 | **89.8** |
| union of both | 91.2% | **96.0%** | **93.5** | 92.1 |

**Three corrections.**

1. **The rules are not 100% precise — they are 91.8%**, 16 wrong of 194. The random batch said
   100.0% because it never drew a contested record. That figure was published in the README and on
   the model card and has been corrected in both.
2. **The teacher beats the student on recall decisively**: 75 found against 35 missed, **p =
   0.00017**. First unambiguous result in this line of work. Precision cost is 11 extra false
   positives against 3 avoided, p = 0.057.
3. **The student's precision edge mostly evaporates on contested records**: 92.5% against 91.1%,
   not 99.6% against 91.8%.

**Neither batch is honest alone.** This one is adversarial by construction and its absolute numbers
do not describe the corpus (57% of teacher-labelled records are contested, not all). The random 96
stays the yardstick for *how good is the pipeline*; this one answers *which source is better*, and
it says the earlier "+2 recall for −4 precision" verdict came from a sample too clean to show the
trade.

**What this reopens.** The teacher's recall advantage is real and large. Rounds 07 and 08 failed to
transfer it into the student's weights, and that remains true — but the reason to keep trying is
now much stronger than it looked, and the inference-time ensemble (union: 96.0% recall) is the
obvious shape.

Lexicon 0.6.1: NDSB-201, isocitrate, UMP, dicoumarol, xylopentaose added; glycyl-glycine,
octyl-beta-glucopyranoside and dimethylethylammonium propane sulfonate resolved as aliases. Six
labels remain unresolvable and are ambiguous abbreviations (L-RHA, PEA, AVA, MG(II)) rather than
gaps.

## 2026-08-03: agreement gating measured — works, still short

A teacher-only find is kept only if a second, architecturally different 32B names the same reagent
independently. Gemma-4-31b, because `chem_sage` is Qwen2-based and would share the first teacher's
failure modes.

| | precision | recall | F1 | F0.5 |
|---|---|---|---|---|
| rules + student *(shipped)* | **99.6%** | 87.4% | 93.1 | **96.9** |
| rules + Gemma alone | 96.3% | 87.8% | 91.8 | 94.4 |
| + every Qwen find | 92.2% | **92.9%** | 92.5 | 92.4 |
| **+ only where both agree** | 96.1% | 91.8% | **93.9** | 95.2 |

Keeps 13 of 16 correct finds, cuts the wrong ones 22 → 10. Best F1 in the project; **still behind
on F0.5 and on precision**, which are the numbers that have decided everything else here.

**Where it leaves the direction.** As a *training* recipe it is dead: the surviving additions are
13 correct to 10 wrong, and round 08 proved this student absorbs label noise rather than averaging
it. As an *inference-time ensemble* it is live and cheap — run the second teacher only on the
teacher-only candidates, a few hundred records, not the corpus.

**Gemma is the better teacher** (96.3/87.8 against Qwen's 91.8/84.3), which was not the reason it
was picked.

### If picking this up again

1. **Three-way agreement.** Qwen3-32B is cached and untried. Two teachers cut the wrong additions
   by 55%; a third may reach the precision bar, at ~100 s/record on the candidate subset only.
2. ~~**The role question, not the chemistry question.** Every surviving error is a reagent that
   *is* in the text but belongs to a protein buffer, a soak or a cryo step. That is a labelling
   distinction the schema already has (`role`) and nobody has trained against.~~ **Both halves
   corrected 2026-08-03**: scope is 55% of the errors rather than all of them, and the schema had
   no vocabulary for it. See the top of this file.
3. ~~**A disagreement-sampled second gold set.**~~ Done 2026-08-03; it is what produced the
   correction above.

### Reasoning models need a different harness

Two silent failures, both worth not repeating:

- **Token budgets do not transfer between models.** 448 was measured on Qwen's output distribution.
  Gemma-4 spends its budget thinking: 6 of 96 generations closed a JSON array. It needs ~2,048 and
  runs at 102 s/record against Qwen's 40.
- **Take the *last* JSON array, never the first.** Gemma quotes the few-shot examples verbatim
  while reasoning, so the first array in its output is the prompt. Parsing it would have scored our
  own examples back as the teacher's labels, and looked entirely plausible doing it.

## 2026-08-02: rounds 07 and 08, and the numbers that were wrong

Both attempts to train past the rule parser's ceiling with 32B-teacher labels scored worse than
round 06, and the second was a clean test of the first's excuses. **The figures below are the
corrected ones** — the originals were produced by a gold set that scored correct answers as false
positives on five records whose labels the lexicon could not resolve, a bias favouring whichever
model says least. This section said "closed" on the strength of those.

| | round 06 final | round 07 | round 08 |
|---|---|---|---|
| Labels | rule parser | teacher, 92.6% precise | teacher, 97.6% precise |
| Rules epochs | 0.94 | 0.23 | 1.06 |
| Precision / recall | **99.6% / 87.4%** | 93.6% / 89.1% | 95.3% / 89.1% |
| F1 / F0.5 | **93.1 / 96.9** | 91.3 / 92.6 | 92.1 / 94.0 |

Round 08 swept on the gold set at 1,000 / 2,000 / 4,000 / 6,000 / 8,000 iterations: F1 93.0–93.9,
**flat**. Paired against round 06: recall +13/−8 (p = 0.38), false positives 1 → 16 (p = 0.0003).

**Consistent trade: about +2 recall for −4 precision**, across a 5-point range of label quality and
a 4x range of training. Not worth it for a released dataset, and unchanged in direction by the
correction — only in size.

**Round 06's final adapter stays shipped.** All rounds are public at
[`Dellboy/toppdblx-residual-parser`](https://huggingface.co/Dellboy/toppdblx-residual-parser),
both regressions documented in the card.

### Where recall improvement has to come from now

Not distillation. The gold set says 25 of 294 reagents are still missed by rules + model; the
teacher located 18 of them, so the *targets* are known even though the teacher cannot teach them:

- **A second, disagreement-sampled gold set.** The current one is 9% informative — 269 of 294
  judgements confirmed something already right. Sampling where student and teacher disagree would
  invert that ratio for the same hour of Marc's time. It measures rather than fixes, but it is the
  only thing that has repeatedly overturned a wrong conclusion here.
- **The 25 gold-named lexicon gaps** — CYMAL-3, NDSB-195, GSH/GSSG, ALF4, rhodium(III) hexamine.
  Human-verified, unlike the model's 8,170-name tail.
- **The parser.** The rules are 100% precise at 71.4% recall; the 84 reagents they miss on the gold
  set are a concrete list, and parser fixes have historically paid better than modelling here.

### Do not repeat

- **Training longer does not rescue noisy labels.** Round 08 was flat across an 8x range of
  iterations.
- **Do not predict a model's precision from its labels' precision.** Round 07 matched, round 08
  amplified.
- **`identification` cannot choose a checkpoint.** Three answers to "how long to train" came from
  it and all three were wrong. Use the gold set.

## Superseded: teacher distillation (2026-08-02)

`models.teacher_label` is labelling ~5,000 residual records with a local 4-bit Qwen2.5-32B, gold
set excluded by name, resumable per record.

    wc -l data/interim/slm/teacher_progress_5k.jsonl        # progress
    tr '\r' '\n' < /tmp/teacher_wired.log | grep labelling: | tail -1

**Why, in one line:** the student is bootstrapped from `rules_v3`, so `rules_v3` is its ceiling,
and the round 06 sweep showed exactly that — fidelity climbing to 93.6% while residual
identification peaked at 2,000 iterations and fell.

**The teacher is worse than the student and that is not the point.** On the 96 gold records the
32B scores 91.0% precision and 89.1% recall against the 360M's 99.6% and 91.5%. But their recalls
are statistically indistinguishable (p = 0.33) and their errors are different: the teacher finds
**18 correct reagents that rules and student together miss**, and the union takes misses from 25
to 7 of 294. A training set drawn from both covers ground neither reaches alone.

Next: build training pairs from teacher–student agreement plus teacher-only finds that pass
grounding, then round 07 at ~2,000 iterations. **Target, explicit and falsifiable: recall above
91.5% without dropping below 99% precision.** If round 07 misses it, the teacher route is closed.

### The machine, and how it misled me three times

The job runs near 101 s/batch for its first fifteen minutes then settles two to five times
slower. Consequences, all learned the expensive way on 2026-08-02:

- **Never size this job from its first progress bar.** A cold 20-minute measurement compared
  against a hot 8-hour one made a batch-size change look like a 5.5x penalty. Per record the real
  difference between batch 8 and batch 16 is about 5%: 38.2 s against 40.2 s.
- **`MAX_TOKENS` was 1024 on a truncation theory that was wrong.** Raising it changed the invalid
  count by zero; the real cause was `bad_unit`. Measured over 720 real generations the longest is
  ~330 tokens, so it is now 448.
- **`mx.set_wired_limit()` is under evaluation** as the fix for the sustained-load slowdown, and
  must be judged on a steady-state window, not on its opening batches.

## Settled 2026-08-01: rounds should be 2,000 iterations, not 6,000

The round 06 checkpoint sweep ran to completion on the frozen benchmark. Identification peaks at
2,000 and then **declines and stays down**:

| iter | fidelity exact | schema valid | identification | CI95 | grounded | fully ident. |
|---|---|---|---|---|---|---|
| 500 | 80.60% | 96.80% | 86.80% | [85.97, 87.59] | 93.66% | 58.15% |
| 1000 | 84.80% | 97.00% | 87.19% | [86.37, 87.98] | 94.42% | 58.45% |
| **2000** | 89.60% | 98.60% | **90.52%** | [89.76, 91.22] | 94.36% | **68.25%** |
| 4000 | 93.20% | 99.05% | 88.91% | [88.13, 89.64] | 94.33% | 62.40% |
| 6000 | 93.60% | 99.10% | 88.99% | [88.20, 89.73] | 93.96% | 62.35% |

2000 → 4000 loses 1.61 points on disjoint intervals, and 4000 → 6000 moves +0.08, which is
nothing. **The plateau claim was wrong, but so was the full-epoch decision**: learning continues
well past 1,000 and stops well before 6,000.

**Fidelity climbs monotonically the whole way, 80.6% to 93.6%, while identification turns over.**
That divergence is the circularity trap made visible: everything after 2,000 iterations went into
imitating `rules_v3` more exactly, which is capacity spent learning what is already in code, and
it came at the cost of the residual. Val loss moved 0.003 across the whole span and pointed at
6,000 throughout. **Watch fidelity as a divergence signal, never as a score.**

**Consequences, revised 2026-08-02.** ~~Promote checkpoint 2000, not the final adapter.~~ That was
wrong, and only the gold set could show it: against hand-labelled truth the final adapter beats
checkpoint 2000, 99.6% precision to 95.7%, one false positive against twelve (p = 0.0034).
Identification cannot see a reagent that is real, in the text, and not what the depositor meant.
**Judge checkpoints on the gold set; the frozen benchmark compares rounds but cannot choose within
one.** Train future rounds for ~2,000 iterations still stands.

**`apply_slm` accepted `--checkpoint` and ignored it** until 2026-08-02, so the 163,353 components
released on 2026-08-01 came from the final adapter rather than the promoted checkpoint. That was
the better model, so the release is unaffected — but every note claiming it used checkpoint 2000
was wrong.

### Correction: `--limit` never applied to the frozen benchmark

An earlier version of this file warned that a sweep passing `--limit 500` scored only 500 of the
frozen set's 2,000 records, and that its figures must not sit beside full-set scores. **That was
wrong.** In `eval_slm.py` the fidelity set is truncated unconditionally but the residual set is
truncated only when `--frozen` is *not* passed:

    valid_rows = valid_rows[:args.limit]
    if not args.frozen:
        residual_rows = residual_rows[:args.limit]

So every `--frozen` identification, schema, grounding and fully-identified figure is already a
full 2,000-record number, whatever `--limit` says. Only fidelity is subsetted. The full re-run of
checkpoint 2000 reproduced the sweep's residual metrics exactly, to the invalid-reason counts,
which is what proved it.

Round 06 checkpoint 2000 against round 05, both full-set: identification **87.58% → 90.52%** on
disjoint intervals, grounded 93.41% → 94.36%, fully identified 64.10% → 68.25%, fidelity 83.90% →
91.22%.

## Waiting on Marc

**Nothing, as of 2026-08-02.** Both accuracy audits and the 96-record gold set are done. What
follows is the record of what they asked and answered.

### Round 2 of the accuracy audit, 2026-08-01

96 fresh conditions on the fixed pipeline. Overall **86.5%** [78.2, 91.9], against round 1's 82.3%.
Rules-derived **85.4%** (identical to round 1); model-derived **87.5%**, up from 79.2%. The
model-side gain is +8.3 points but not significant on independent samples of 48 (p = 0.27).

Pooled over both rounds, 192 judgements: rules **85.4%** [77.0, 91.1], model **83.3%**
[74.6, 89.5], difference +2.1 points, **p = 0.691**. No evidence the model's conditions are
classified worse than the rules', now across two independent samples.

The fifth cause added for round 2 — "the class is right, I am reporting a bad reagent" — fired
once (3AMB) and correctly kept a good classification out of the numerator.

## Done 2026-08-01: the first accuracy audit, and the three fixes it bought

96 conditions judged. **Overall 82.3%** [73.5, 88.6]; rules-derived **85.4%** [72.8, 92.8],
model-derived **79.2%** [65.7, 88.3]. The 6.2-point gap is **not significant** (z = 0.80,
p = 0.42), so there is no evidence the model's conditions are worse than the rules' — and equally
none that they are equivalent, since 48 a side cannot resolve a gap smaller than about 15 points.
Verdicts in `data/interim/class_audit_verdicts.json`.

The failure modes differ, which is the more useful result: 7 of the model's 10 errors were missed
reagents, against 2 of the rules' 7. The model under-reads; the rules mis-gate.

Three fixes, each traced to a specific flagged record:

1. **Explicitly stated cryoprotectants no longer name the class** (`assign.classify`). 1V6H's
   "10% w/w of glycerol was added for cryoprotection" was read correctly and then counted as an
   organic, turning a PEG condition into Organic/PEG. **176 conditions changed class.** Only
   `cryo_evidence == "explicit"` is excluded; extending it to the inferred majority would move
   14,825 but acts on a guess.
2. **Three lexicon entries**, lexicon 0.4.0 → **0.4.1**, 527 reagents and 1,293 names: `CHOLINE`
   as its own entry (3P03), `2-oxyglutarate` (3THP), `1,6-hexnediol` and `1,6 hexnediol` (2ATB).
   Both separators are needed because **nothing normalises a hyphen to a space** — the
   hyphenated forms that resolve today do so via `display_name`, not any alias rule.
3. **Two clause-splitter fixes** (`parse/text.py`). A reagent followed by setup prose keeps its
   reagent, so 7O5Q and 7NRJ stop losing `10% 1-BUTANOL` to "mixed with the protein stock"; and a
   section label written mid-string with no comma no longer glues two components together, so
   3ZY1 keeps both its NaCl and its PEG 8000.

**All three are now in the data.** Re-parsed, `apply_slm` and `classify` re-run on 2026-08-01.
Components identified **514,042 → 515,872**, and the identification rate held *exactly* flat at
0.8518 (chemistry-only 0.8753 → 0.8755). Coverage 77.2%. All six flagged records now parse
correctly: 7O5Q and 7NRJ recover `BUTANOL_1`, 3ZY1 `PEG_8000`, 2ATB `HEXANEDIOL_16`, 3THP
`OXOGLUTARATE_2`, 3P03 `CHOLINE`.

**`apply_slm` did not need its 4h 42m again.** Generation is deterministic (`temp=0.0`) and the
prompt is the deposition text, which a re-parse does not alter, so the progress file was filtered
to the new residual instead: 52,079 of 52,817 rows reused unchanged, 738 dropped because the rules
now read those records, and only **176 newly-residual records generated**, in 49 seconds. Do this
on every re-parse — but filter the progress file first, because `apply_slm` builds its output by
iterating the progress file rather than the residual, so stale rows would otherwise leak in.

### The trap in fix 3, worth not re-stepping in

Truncating a clause at its trailing setup prose **on shape alone made things worse**: 12,449 extra
components for 1,852 more identifications, dropping the identification rate 1.4 points to 0.8376.
Measured over 60,000 conditions, the cut produced 3,752 unidentified heads against 221 real ones —
`ul`, `protein at 10`, `nacl was`, `set up in a 1:1`. Requiring a digit *and* a letter does not
help: "protein at 10" has both.

The cut is now gated on the caller recognising the head as a reagent. `text.py` cannot ask the
lexicon itself, being the module the lexicon is built on, so `clauses_detailed` takes an optional
`is_reagent` predicate and `rules.py` supplies one from its index. Without a predicate nothing is
cut, so every other caller keeps its old behaviour.

### Done 2026-08-01: the plausibility floor

An amount that cannot be true is dropped, and the reagent kept: above **8 M equivalent** or above
**100%**. `quantity.is_implausible` is the predicate; both the rule parser and `apply_slm` apply
it, because the model reads the same depositions and reproduces the same impossible numbers.
238 records flagged `implausible_concentration`, 340 model-read amounts dropped. Nothing above
the floor remains in the corpus. Coverage 77.2% → **77.1%**, the 216 conditions moving to
`no_amount`, which is the honest description of a condition whose only stated number is wrong.

**8 M comes from the corpus, not from a solubility table.** Below it the reagents named at those
strengths are the very soluble ones and the readings are real — sodium formate, sodium chloride
and ammonium nitrate fill the 3–8 M bands, 3,188 components. From 8 M up the names are reagents
that cannot reach it: ammonium sulfate at 8–10 M against a saturation of 4.1 M, Tris and DTT at
10–12 M, zinc chloride at 10 M.

**It is a floor on absurdity, not a solubility check**, and must not be sold as one: `6 M ammonium
sulfate` is equally impossible and passes. Refusing it needs a per-reagent limit and the lexicon
carries no solubilities. A test pins this so the guard is not mistaken for something stronger.

### Bugs the plausibility work surfaced, none of them fixed

The guard now masks these in the output, which makes writing them down matter more, not less.

- ~~**A name-number and a percentage are being read as a range.**~~ **Fixed 2026-08-01.** In
  *trailing* position a descending pair is a name and an amount, not a range: `peg3350-26%` is
  PEG 3350 at 26%, and stripping the whole match had left a bare `peg` that identified to
  nothing, so the amount was absurd *and* the reagent was lost. The rule is confined to the
  trailing form because a **leading** descending pair is a genuine backwards range —
  `2.0-1.8 M ammonium sulfate` means what it says. Measured: 22,459 ascending ranges, 96
  descending, of which only 12 sit in trailing position and every one was this bug. It was never
  only PEG: `mgso4 - 0.15m` took the 4 from the formula and `nano3 - 0.1m` the 3.

  Fixed in `quantity.extract` **and** `text.strip_quantity` together, because one reads the
  amount and the other the name, and reading them off different splits of one string is the
  disagreement that once put 17,256 molecular weights in the concentration column.

  **One case survives, knowingly.** `cacl2-400mm` (8PHI) pairs the 2 of CaCl₂ with 400
  *ascending*, so the rule does not reach it, and calcium chloride still reads 201 mM. The
  general form would be "the first number is glued to a letter", but that is 10 occurrences of
  which 5 are `saturated ammonium sulphat25-50%` — a typo gluing a **real** range to the name,
  where 37.5% is currently correct. Separating the two needs the lexicon (`peg3350`, `cacl2`,
  `mgso4` resolve; `ammonium sulphat25` does not), which `quantity` cannot reach. Not worth a
  lexicon gate for one record.
- ~~**PEG molecular weights in smear enumerations become concentrations.**~~ **Fixed 2026-08-01.**
  A smear is written as one enumeration — `PEG Smear Medium (PEG 2000, 3350, 4000, and 5000 MME)`
  — and the split leaves the last member without its `PEG`. Read as a leading quantity it became
  `MME`, an additive defaulting to millimolar, at 5000 mM: five molar of a reagent that is really
  PEG MME 5000. The prefix is now restored before anything reads the number, in
  `text.strip_quantity` and `quantity.extract` together. Bare `MME` components 32 → 23, and the
  members that sit inside the smear's brackets are now correctly skipped as enumeration members
  rather than becoming components: the bracket guard keeps a clause only when it carries a
  quantity, and with the molecular weight no longer read as one, they fall out as intended.

- **Amounts joined across line breaks: not fixed, and should not be.** `0.1M Sodium\n659 Acetate
  Trihydrate` (6B5L), `25%\n480 ethylene glycol` (6G8A), `\n305 Isopropanol` (5TSU). The stray
  number opens a clause and is read as a unitless amount — but **that shape is indistinguishable
  from a legitimate deposition**. Of 114 clauses opening with a unitless 3+ digit integer, most
  are real: `150 nacl`, `100 malonate`, `100 tris ph 8.5`. Separating them needs to know that 659
  is absurd *for sodium acetate*, which is per-reagent solubility again. The plausibility floor
  already gives the right outcome — reagent kept, stray amount dropped, record flagged — so this
  is closed rather than open.

### Done 2026-08-01: spelled-out unit words

Found while measuring the line-break case, and worth more than either bug it was found beside.
**1,409 occurrences where the depositor stated the unit in words and the parser discarded it**,
leaving `infer_unit` to guess on a value whose unit was never in doubt. Counted: millimolar 533,
molar 239, microm 169, percent 166, micromolar 96, mol/l 82, mmol/l 38, mol 38, mmol 14,
millim 11, per cent 9, plus hyphenated and spaced spellings of each.

`UNIT_WORDS` now lives in `parse/text.py` and is imported by `parse/quantity.py`, so the two
cannot drift; `_canonical_unit` strips hyphens as well as spaces, so `milli-molar` and
`per cent` need no separate map entries.

Identification **515,878 → 516,241**, rate 0.8519 → **0.8526**, `unidentified_reagent` as a
blocking reason 653 → 632. Components fell 605,592 → 605,481, which is the point: a unit word
was previously part of the reagent's name and made a clause that identified to nothing.

**Volume words are deliberately excluded.** `ul` (6,395), `nl` (1,364), `ml` (1,136) and
`microliter` (940) are each commoner than any concentration word here, and every one describes
the drop rather than the chemistry. A test pins this, along with `mole`, `molybdate` and
`micrometer`, which merely start like units.

**`unit_inferred` rose 192,146 → 192,578, and that is not a regression.** `12 PERCENT MEPEG 2000`
now identifies where the whole clause used to fail, and `percent` maps to `percent_unspecified`
because the word genuinely does not say w/v or v/v — so chemistry resolves it and the flag is
set, honestly. Of the 4,322 components in records using a spelled-out unit, 3,427 carry an
explicit one.

### Still open from the audit

- **The inferred-cryo question.** 17,708 components are `role=cryo` by inference against 2,112
  explicit. Deciding how far to trust that inference is worth 14,825 conditions.
- **The audit cannot separate "wrong class" from "right class, wrong parse".** 3P03 was flagged
  wrong but corrected to the class it already had, because the only way to record a parse error
  was the wrong-class checkbox. Excluding it the model reads 80.9%. Courtroom v7 needs a fourth
  option.

## Commercial screens: one vendor of six done

`assign.build_screens` handles Hampton's numbered-line binders, exhaustively: all 59 published
catalogue numbers probed, 19 screens and 894 wells kept, the rest rejected by guard.
`assign.build_vendor_screens` handles A1-H12 plate layouts and currently carries only Morpheus.

Adding a screen is one entry in `SOURCES` (vendor, catalogue, screen, url, expected_wells), but
each needs its formulation document located and **verified after extraction**, not just accepted
because the well count matched.

| Vendor | State |
|---|---|
| Hampton Research | done, exhaustive |
| Molecular Dimensions | Morpheus and PACT premier done. Still to do: Morpheus II, Morpheus Fusion, Structure Screen, MIDAS. `MD1-37` JCSG-plus rejected, its brochure has no plate table in the text layer; the NeXtal formulation covers the same screen |
| Rigaku Reagents | reachable, not started |
| Jena Bioscience | reachable, not started |
| MiTeGen | reachable, not started |
| Qiagen / NeXtal | JCSG+, JCSG Core I, JCSG Core IV done (288 wells) |
| Emerald Bio | reachable, not started |

~~Do JCSG-plus and PACT first.~~ Done. They are the screens the corpus actually names: `pact`, `jcsg`
and `proplex` appear in deposition text and are currently caught only as screen *references*,
never matched to a formulation.

**Verify every extraction by content, not by count.** Morpheus `H12` first came out as
`Morpheus FX-96 MD1-47-FX`, a product code. That brought the total to exactly the expected 96, so
the shortfall guard passed: 95 real conditions plus one artefact is indistinguishable from a
complete plate by counting alone. Check that every well states a concentration.

## Done 2026-08-01: the model is in the data

`models.apply_slm` ran for the first time, on checkpoint 2000. Output
`data/interim/slm_components.parquet`, 166,233 components across 50,818 records, every row
`parser = slm_v1` and `parse_confidence = 0.7`.

| | |
|---|---|
| residual records offered | 52,817 (not the 59,673 the docstring claims; curation shrank it) |
| records the model read | 50,930 |
| components kept | 166,233, of which 17,490 are `not_a_component` |
| dropped, name not in lexicon | 17,967 |
| dropped, not in the text | 2,903 |
| dropped, invalid JSON | 706 |
| kept as typo correction | 5,526 |

`assign.classify --slm-components …` then merged it. **Coverage 59.50% → 77.18%**, and
`unidentified_reagent` as a blocking reason collapsed from 39,679 to 654, a 98.4% reduction.
The other reasons grew — `no_amount` 24,333 → 26,179, `no_precipitant` 6,289 → 9,634, `mixture`
5,096 → 6,033 — because records previously stuck at the first blocker now reach the next one.
Classified rose 110,785 → 143,682, which is exactly the 32,897 freed net of those.

**The lexicon is now the binding constraint, not the model.** 17,967 names were dropped purely
because `synonyms.yaml` has never heard of them: 9.6% of everything generated, and six times the
invention rate. Candidates named by the frozen run include `MALONATE`, `PHOSPHATE`, `PEG`,
`JEFFAMINE`, `AMMONIUM_DIHYDROGEN_PHOSPHATE`, `TEA`, `SULFATE`. Lexicon additions now buy more
coverage than more training does.

Baseline snapshot kept at `data/interim/condition_classes.prelsm.parquet` for before/after work.

## Known gaps, each a deliberate choice

- **Morpheus stocks are unexpanded.** The brochure defines them (`Divalents 0.3M Magnesium
  chloride hexahydrate; 0.3M Calcium chloride dihydrate`), so `NPS`, `precipitant mix` and
  `precipitant mix 4` in the unidentified head could be resolved from the vendor's own stock
  table. That is a second extraction pass over the same document.
- **JCSG+ HT (HR2-150) is still refused**, yielding 8 conditions with no tube list to bound its
  columns. It is the HT presentation of HR2-145, which now ships complete at 96, so what is
  missing is a catalogue alias rather than any chemistry.
- **Molecular Dimensions is four screens of a much larger catalogue.** MIDAS, Structure Screen,
  MemGold and Morpheus II are all still absent; MIDAS was downloaded and prints neither a
  box-well nor a plate layout the current extractors recognise.
- **StockOptions kits will never ship and should not.** HR2-095, 100 to 106 and 251 to 257 are
  single-buffer pH series, stock solutions rather than crystallisation screens. They are
  reported as rejections only because they sit in the same catalogue range.
- **Eight partner screens are extracted but refused**, each for a stated reason: Wizard
  Precipitant Synergy is two 96-condition screens printed as one numbered run of 192 and only
  191 read; JBScreen LCP came out 95 of 96; JCSG++ 1 to 4, Basic 2 and Membrane 3 fail the
  sibling-size check, because the brochure prints these families in fours of equal size and a
  member disagreeing with every sibling is the signature of a heading carried past its block.
- **Rigaku's conjugate-pair buffer notation is the largest lexicon gap in the screen library**
  ("HEPES/ Sodium hydroxide", "Imidazole/ Hydrochloric acid", "Sodium acetate/ Acetic acid").
  Splitting on the slash and identifying the first half would recover most of it, but it needs
  care: the pair names a buffer system, and treating the conjugate acid as a separate reagent
  would invent a component the vendor did not list.
- **PEP 629 and PEE 270** are pentaerythritol propoxylate and ethoxylate, confirmed by Jena's
  own product listing rather than inferred. 96 wells of the Pentaerythritol series depend on
  them and they are unambiguously real, so they are the cheapest lexicon win available.
- **Four Hampton HT binders reconstruct cleanly but ship nothing.** Crystal Screen HT, MembFac
  HT, Index HT and Grid Screen Salt HT are column-only layouts whose binders print no tube list,
  so there is no independent witness to a dropped line and they are refused. Crystal Screen HT
  reconstructs all 96 and matches Crystal Screen 1 and 2 exactly, which is what validated the
  column extractor, but it is a duplicate so nothing is lost. Index HT is the warning: it
  reconstructed a well-formed 48 for a 96-well product.
- **Low Ionic Strength Screen (HR2-120) still fails**, because a page header interrupts its
  precipitant column six entries in, leaving 6 against the buffer column's 18. Skipping page
  furniture would recover 18 conditions, but it also weakens the equal-length check that is the
  only thing standing between a positional zip and invented chemistry.
- **The three NeXtal suites read at 84 to 90%, every other screen at 100%.** Their PDF text layer
  runs the salt, buffer and precipitant columns together and splits numbers at kerning pairs, so
  `hepes ph 7 .5 10% peg 8000`, `peg 6000 4.0` and `ammonium sulfate 25,5% peg 4000` arrive as
  single unreadable clauses. `_SPLIT_NUMBER` and `_NEW_AMOUNT` in `assign.build_vendor_screens`
  repair some of it; the remainder needs a comma-decimal rule and a split on a pH value followed
  by a percentage. These are extraction artefacts and must never be added to the lexicon as
  reagents, which is what makes them look like a curation queue when they are not.
- **59 buffers still have no pKa**, so they cannot be lexicon entries. Many are mixed systems
  with no single pKa; the rest are clause-splitter failures and are better fixed in the parser.
- **24,327 conditions (13.1%) stay Unclassified for want of a stated amount.** Classifying them
  would lift coverage from 59.5% to about 72% in one line. Decided against on 2026-07-31: a
  condition with no concentration is not a measured condition. Reasoning is in `classify.py`.

## Standing hazards, learned the hard way here

- **Never write `until ! pgrep -f "..."`.** The wait loop matches its own command line and never
  exits. Cost over two hours in one instance and 75 minutes in another. Poll for the output file.
- **Verify a process by CPU and RSS, not by name.** A sleeping wrapper shell looks identical to a
  running trainer in `ps`.
- **A mechanical rename crosses boundaries a test suite cannot see.** The resolution to
  identification rename broke `Path.resolve()`, corrupted a column name in a historical document,
  altered the wording of Marc's own brief, and left serialised JSON keys pointing at nothing.
