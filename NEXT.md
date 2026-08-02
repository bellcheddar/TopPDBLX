# What to do next

Written 2026-07-31 so that nothing outstanding lives only in a conversation. Everything here is
either running, generated and waiting for an answer, or specified and not yet built.

## Closed 2026-08-02: teacher distillation, after two rounds

Both attempts to train past the rule parser's ceiling with 32B-teacher labels failed, and the
second was a clean test of the first's excuses.

| | round 06 final | round 07 | round 08 |
|---|---|---|---|
| Labels | rule parser | teacher, 92.6% precise | teacher, 97.6% precise |
| Rules epochs | 0.94 | 0.23 | 1.06 |
| Precision / recall | **99.6% / 91.5%** | 92.3% / 93.9% | 94.5% / 93.2% |
| False positives | **1** | 23 | 16 |

Round 08 swept on the gold set at 1,000 / 2,000 / 4,000 / 6,000 / 8,000 iterations: F1 93.0–93.9,
**flat**. Paired against round 06: recall +13/−8 (p = 0.38), false positives 1 → 16 (p = 0.0003).

**Consistent trade: +2 recall for −5 precision**, across a 5-point range of label quality and a 4x
range of training. Not worth it for a released dataset.

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
