# Ontology changelog

Spec 6.6 requires the ontology to be versioned semantically with a changelog, because every
model trained afterwards is tied to a specific ontology version and must record it.

Two artefacts are versioned here, independently:

| File | Version | What it is |
|------|---------|------------|
| `synonyms.yaml` | 0.1.0 | Reagent lexicon: canonical ids, aliases, PEG molecular weights, Hofmeister ranks, buffer pKas |
| `groups.yaml` | 0.1.0 | Condition group ontology: L2 and L3 group definitions with centroids and screen anchors |

## groups.yaml

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

- **20,329 conditions (11.1%) have no identified precipitant** and are deliberately given no
  group. Giving them a centroid would dress an absence of evidence up as a chemical claim.
  This is the largest hole in the ontology and the best target for lexicon curation.
- **One candidate group was dropped** for having no measurable axis on any member: nothing
  could ever have been assigned to it.

## synonyms.yaml

### 0.1.0 (2026-07-29)

147 reagents, 501 names. Seeded from a frequency-ranked mining of 198,918 records, then
corrected across two rounds of expert audit (35 questions, then 13). Includes PEG molecular
weights, Hofmeister ranks spanning sulfate (-4) to thiocyanate (+4), and buffer pKas.

Phosphates are split by protonation state, since monobasic and dibasic are not
interchangeable as standalone reagents (about pH 4.5 and pH 9 respectively at 0.1 M).
