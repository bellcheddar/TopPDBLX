# What to do next

Written 2026-07-31 so that nothing outstanding lives only in a conversation. Everything here is
either running, generated and waiting for an answer, or specified and not yet built.

## Running now

**Round 06** is training: 6,000 iterations (a full epoch), LoRA rank 16, 99,189 pairs including
6,856 whose correct answer is no chemistry at all. Log at `data/interim/slm/train_r1_round06.log`.

When it finishes:

1. Sweep its checkpoints on the frozen benchmark, which is the point of the run:
   `./run.sh models.eval_slm --frozen --checkpoint N --adapter-dir data/interim/slm/runs/r1-parse-residual-smollm2-360m-round06`
   for N in 250, 1000, 2000, 4000, 6000.
2. **This settles the epoch question.** An earlier claim that identification plateaus at 0.09 of
   an epoch was measured on training data that was 36% duplicates, against a residual that shrank
   from the easy end each time curation improved. Neither holds now. If identification and
   grounding are still climbing at 6,000, the plateau claim was an artefact and longer runs are
   worth it; if flat from ~600, it was right for the wrong reason.
3. Then run `models.apply_slm`, which has never been run. It is the only thing that would convert
   the model from a measurement into data: the released database currently contains **zero**
   model-derived components after six training rounds.

## Waiting on Marc

`data/interim/class_audit_questions.json`, 8 questions, drop on `app/condition_courtroom_v5.html`.
Each lists 25 conditions of one class and asks only how many are wrong, because an error *rate* is
what the accuracy figure needs and a count gives the same estimate for an eighth of the answers.

Best answered **after** `apply_slm`, so it can be regenerated stratified by provenance: the same
8 answers then give accuracy for rules-derived and model-derived conditions separately, which is
what decides whether the model earned its place.

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
