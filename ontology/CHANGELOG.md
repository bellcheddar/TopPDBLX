# Ontology changelog

Spec 6.6 requires the ontology to be versioned semantically with a changelog, because every
model trained afterwards is tied to a specific ontology version and must record it.

Two artefacts are versioned here, independently:

| File | Version | What it is |
|------|---------|------------|
| `synonyms.yaml` | 0.2.0 | Reagent lexicon: canonical ids, aliases, PEG molecular weights, Hofmeister ranks, buffer pKas |
| `groups.yaml` | 0.3.0 | Withdrawn at 0.3.0: classification is now the seven JCSG Top96 precipitant classes, in `assign.classify` |

## groups.yaml

### 0.3.0 (2026-07-31)

**The three-level ontology is withdrawn. Classification is now the seven JCSG Top96 precipitant
classes and nothing else**, implemented in `assign.classify`.

The L2 and L3 groups were derived by binning the corpus on precipitant class, PEG molecular
weight and salt family, then having labels retrofitted. Spec 6.1 opens by rejecting exactly that:
groups are to be hand-defined and human-readable, with clustering used only as a diagnostic. The
binned groups were never chosen by anyone, and repeated attempts to name them failed because
several were not chemically coherent: median purity across the 41 L2 groups was 49%, and five
were named for a molecular-weight band their own commonest member fell outside.

The replacement is presence-based with **no thresholds**. A PEG is a PEG whatever its molecular
weight and concentration; a salt is a salt whatever its chemistry and concentration. Three
cutoffs that were in the code and not in the brief are gone: PEG below 600 reclassified as an
organic, salt below 0.2 M ignored, and any PEG or organic below 4% ignored.

| Class | Conditions | Share |
|---|---|---|
| Unclassified | 87,885 | 47.7% |
| Salt/PEG | 40,115 | 21.8% |
| PEG | 19,652 | 10.7% |
| Salt | 17,193 | 9.3% |
| Organic/PEG | 6,142 | 3.3% |
| Organic/PEG/Salt | 5,653 | 3.1% |
| Organic/Salt | 5,188 | 2.8% |
| Organic | 2,403 | 1.3% |

**Unclassified is an answer, not a failure**, and at 47.7% it is the largest one. The reason is
always recorded: unidentified reagent (29.9%), no amount stated for a precipitant (12.5%), no
precipitant at all (3.0%), premixed system (2.3%).

**This settles spec 6.4**, open since Phase 0 deferred it as "the taxonomy is Phase 1 and can
wait". Morpheus, PACT and Tacsimate style premixes carry an acid mix, an alcohol mix and a buffer
system at once and do not fit a seven-class taxonomy, so they are Unclassified. The components
remain expanded with their `premix_id`, so nothing is lost and the decision is reversible.

The screen cross-reference is unaffected and still carried: spec 6.5's payoff is that output
becomes orderable, and a screen well is orderable whatever class its condition falls in.

### 0.2.0 (2026-07-30)

First curated version: 163 groups (41 L2 covering 70.7% of 183,623 usable conditions, 122 L3
covering 39.2%), built from the corpus plus the curation answers in
`data/interim/audit_rounds/groups_round1_answers_20260730.json`. Curation answers are now a
build input, so `groups.yaml` is reproducible from corpus plus answers rather than hand-patched.

All 26 questions were answered with the recommended option, so nothing was merged and nothing
dropped. Two answers asked for a group to be created:

- **Honoured:** `Salt/PEG · PEG 2000 · sulfate/phosphate/citrate · 20-30% · 0.1-0.3 M · pH 4-5`
  (279 records). Its L2 parent was promoted alongside it, since an L3 group with no parent in
  the ontology would have been silently discarded.
- **Not honoured, and reported rather than ignored:** `Organic · pH unstated` (530 records).
  Not one member has a single measurable axis, so no centroid exists and nothing could ever be
  assigned to it. That request exposed an inconsistency in L1, now fixed: the salt branch
  required a measurable concentration while the organic and PEG branches did not, so L1 was
  calling a condition "Organic" when nothing quantitative was present.

Coverage fell from 0.1.0 (81.3% L2) because of that fix, not because of curation. The drop is
the ontology becoming honest rather than worse.

### 0.1.0 (2026-07-29)

First proposal, derived from the corpus by `./run.sh assign.build_groups` and not yet
hand-curated. 181 groups: 40 at L2 covering 81.3% of 183,462 usable conditions, and 141 at L3
covering 46.5%, with conditions outside any L3 group falling back to their L2 parent.

134 of the 141 L3 groups are anchored to a real commercial screen well, so the output can name
something orderable rather than a set of numbers.

Sizing follows the diagnostic in `assign.diagnose`, which answers open decision 12.1 with
evidence. The brief's L2 estimate of about 30 groups is sound: 39 cells reach 90% of the
corpus. Its L3 estimate of 96 to 150 covers only about half, not most: 116 cells reach 50% and
1,118 would be needed for 90%. That is the long-tailed distribution the brief predicts, and it
is why L3 falls back to L2 rather than being expanded tenfold.

**Not yet curated.** Every group has a machine-generated label, a centroid averaged over its
members, and its record count. The next step is Marc's: merge groups that are chemically the
same, split any that are not, and replace the generated labels with names a crystallographer
would use.

Known gaps recorded rather than hidden:

- **40,466 conditions (22.0%) have no identified precipitant** and are deliberately given no
  group. Giving them a centroid would dress an absence of evidence up as a chemical claim.
  This is the largest hole in the ontology. It is not mainly a parsing failure: about 22,000
  precipitant components genuinely state no amount in the source text ("PEG 3350, 0.02M Citric
  Acid, 0.08M Bis-Tris-Propane" names the PEG but never says how much). A concentration-based
  ontology cannot place them, and inventing a concentration would be worse than admitting it.
- **One candidate group was dropped** for having no measurable axis on any member: nothing
  could ever have been assigned to it.

## synonyms.yaml

### 0.2.0 (2026-07-30)

165 reagents, 570 names, from the R1 lexicon-gap audit: 40 questions ranked by corpus frequency,
each standing for every occurrence of that string, covering 7,018 unidentified components. Built by
`parse.lexicon_questions` and applied by `parse.apply_lexicon_answers`, so this version is
reproducible from corpus plus answers rather than hand-patched.

**18 new reagents** (2,895 components): SPG, NAD, PLP, GLUCOSE, GLYCEROL_ETHOXYLATE, BENZAMIDINE,
TRIS_ACETATE, PEG_3400, PEG_3500, BARIUM_CHLORIDE, IMIDAZOLE_MALATE, PROPANEDIOL_13,
POTASSIUM_CACODYLATE, STRONTIUM_CHLORIDE, MOPS_HEPES, FAD, SODIUM_MALEATE, AMPPNP.

**13 aliases** onto existing entries (1,962 components), including `ca(oac)2`, `ammso4`,
`naformate` and `na-formate`, and `sodium citrate tribasic dehydrate`.

Two answers were expert overrides of the recommendation: `bicine/trizma base` to TRIS_BICINE
rather than a new entry, and `imidazole malate` to a new premix rather than MES_IMIDAZOLE. The
remaining 38 were accepted recommendations, and each applied change records which it was.

**Three recommendations were overridden during application**, because accepting them would have
written bad chemistry into the ontology. The recommender is fuzzy string matching with chemistry
guards, and on these it was wrong:

- `methyl-2,4-pentanediol` (216) is MPD, added as an alias rather than as a duplicate entry that
  would have split those components away from MPD's group.
- `peg 2k mme` (133) is PEG MME 2000, added as an alias. The digit guard had refused the match
  because "2k" and "2000" differ as strings, which is the guard working correctly on a case it
  cannot know about.
- `peg 8000 5-7.5% glycerol 1 mm cucl2 100 mm sodium cacodylate` (149) is an entire condition
  captured as one clause, a splitter defect rather than a reagent. Recorded in
  `data/interim/splitter_defects.json`.

**Six strings were deliberately left unidentified** (1,694 components): `peg` (720), `phosphate`
(358), `propanediol`, `citrate buffer`, `polyethylene glycol`, `butanediol`. Each names a family
without naming a member, so any specific entry would be invented rather than identified.

**PCTP and PDTP were held back** (341 components). Both are mixed buffer systems, and the lexicon
requires a premix to list its constituents and a buffer to carry a pKa. Neither formulation was
confirmed during the audit, and a mixed system spanning a pH range has no single pKa. Inventing
constituents to satisfy the schema would defeat the purpose of the invariant, so they remain
unidentified pending a verified formulation.

### 0.1.0 (2026-07-29)

147 reagents, 501 names. Seeded from a frequency-ranked mining of 198,918 records, then
corrected across two rounds of expert audit (35 questions, then 13). Includes PEG molecular
weights, Hofmeister ranks spanning sulfate (-4) to thiocyanate (+4), and buffer pKas.

Phosphates are split by protonation state, since monobasic and dibasic are not
interchangeable as standalone reagents (about pH 4.5 and pH 9 respectively at 0.1 M).
