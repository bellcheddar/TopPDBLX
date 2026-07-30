# Ontology changelog

Spec 6.6 requires the ontology to be versioned semantically with a changelog, because every
model trained afterwards is tied to a specific ontology version and must record it.

Two artefacts are versioned here, independently:

| File | Version | What it is |
|------|---------|------------|
| `synonyms.yaml` | 0.1.0 | Reagent lexicon: canonical ids, aliases, PEG molecular weights, Hofmeister ranks, buffer pKas |
| `groups.yaml` | 0.1.0 | Condition group ontology: L2 and L3 group definitions with centroids and screen anchors |

## groups.yaml

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

### 0.1.0 (2026-07-29)

147 reagents, 501 names. Seeded from a frequency-ranked mining of 198,918 records, then
corrected across two rounds of expert audit (35 questions, then 13). Includes PEG molecular
weights, Hofmeister ranks spanning sulfate (-4) to thiocyanate (+4), and buffer pKas.

Phosphates are split by protonation state, since monobasic and dibasic are not
interchangeable as standalone reagents (about pH 4.5 and pH 9 respectively at 0.1 M).
