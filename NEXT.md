# What to do next

Written 2026-07-31 so that nothing outstanding lives only in a conversation. Everything here is
either running, generated and waiting for an answer, or specified and not yet built.

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

Consequences: promote **checkpoint 2000**, not the final adapter; train future rounds for ~2,000
iterations; keep judging rounds on residual identification.

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

`data/interim/class_audit_questions.json`, now **96 conditions**, drop on
`app/condition_courtroom_v6.html`. One condition per screen: deposition text, the reagents found,
the class it was given, and a single checkbox for "wrong class", then Next. Space ticks, Enter
advances, so the whole set is a few minutes of keyboard work. Clicking Next on an untouched card
records "seen, judged correct" — that is what keeps the denominator honest.

Sized at 96 rather than the earlier 400: individual verdicts give exact counts instead of bands,
and 48 per side is about ±8 points on each, which is coarse but enough to decide the only question
being asked.

Regenerated stratified by provenance now that `apply_slm` has run: eight classes × {rules-derived,
model-derived}, so the answers give **separate accuracy figures for rules and for the model**,
which is what decides whether the model earned its place. A single blended number cannot answer
it, because the model's conditions are by construction the ones the rules found hardest.

Model-derived means the model contributed a reagent the rules missed, not merely that it ran on
that record — reagents it merely reproduced are attributed to the rules and tagged accordingly.
Reagents marked `[model]` in the listings are the model's own.

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
