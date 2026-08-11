# TopPDBLX: a parsed, normalised and sequence-linked database of crystallisation conditions from the Protein Data Bank

**Marc C. Deller**
ORCID [0000-0001-8070-6502](https://orcid.org/0000-0001-8070-6502)

Dataset: [10.5281/zenodo.21807134](https://doi.org/10.5281/zenodo.21807134) ·
Code: [github.com/bellcheddar/TopPDBLX](https://github.com/bellcheddar/TopPDBLX) ·
Explorer: [toppdblx.mdeller.com](https://toppdblx.mdeller.com)

> **Draft.** Every figure below is generated from the released artefacts and re-checked against
> them, not copied from earlier prose. Where a number differs from an earlier internal document,
> the released artefact wins.

---

## Abstract

The Protein Data Bank records the crystallisation condition for almost every structure it holds,
in a single free-text field typed by hand by a different depositor each time. That text is the
largest existing record of what actually crystallises proteins, and it is effectively unqueryable.

We parse the whole archive into typed components: 199,185 crystallisation records from 198,691
entries, yielding 645,656 reagents with role, concentration and unit, of which 87.3% resolve to a
canonical reagent from a curated 500-reagent lexicon. Every record is linked to the construct
sequence that produced it, with MMseqs2 cluster identifiers at 30%, 50% and 90% identity so that
redundancy can be controlled. 81,802 records are matched to a well in one of 59 commercial
screens, transcribed verbatim from vendor materials.

Four findings ran against expectation and are the more interesting part of the work. A
deterministic rule parser reaches **94.4%** coverage where 75% was expected, because the hard part
is clause splitting rather than chemistry. Archive fidelity is exact across 205,949 entries.
**One usable record in six names a precipitant and never states how much of it was used.** And a
query-independent frequency prior beats sequence-based condition prediction at every level tested,
by both homology retrieval and a learned model, which says the archive supports choosing screens
by precedent but not by protein.

---

## 1. Why this is hard, and why it is worth doing

The strongest lever on crystallisation success is construct design, and the second strongest is
choosing a screen that suits the protein. Both are still done largely by intuition, because the
precedent sits in a quarter of a million unstructured strings that nobody can query.

A single deposited condition looks like this:

```
0.1M HEPES pH 7.5, 20% w/v PEG 3350, 0.2M ammonium sulfate, VAPOR DIFFUSION,
SITTING DROP, temperature 291K
```

and the next one like this:

```
protein was concentrated to 12 mg/ml in 25mM Tris/HCl pH 7.5 100mM NaCl and
crystals grew from 1.6-2.0M sodium formate after streak seeding, see also PMID 27658368
```

Both are the same field. The second contains a protein storage buffer that never touched the
crystallisation drop, a concentration range, a method note, and a literature reference. Reading
the first is a parsing problem; reading the second correctly is the whole difficulty.

## 2. Building the dataset

### 2.1 Fidelity first

Every condition string was fetched over the RCSB GraphQL API and then **byte-compared against
`_exptl_crystal_grow.pdbx_details` parsed from the archive mmCIF**. Across 205,949 entries the
agreement rate is exact: no silent transport corruption, no encoding drift, no truncation. This is
reported first because every downstream number depends on it and it is cheap to verify and
expensive to assume.

### 2.2 The rule parser, and where it stops

Clauses are split, amounts and units read, and each reagent name looked up in a curated lexicon.
**94.4% of records parse**; the remainder carry one of seven discard codes with the raw text
retained:

| Discard reason | Records |
|---|---|
| `TOO_SHORT` | 4,369 |
| `NO_REAGENT_MATCH` | 3,934 |
| `METHOD_ONLY` | 2,454 |
| `UNPARSEABLE_RESIDUAL` | 122 |
| `REFERENCE_ONLY` | 103 |
| `EMPTY` | 91 |
| `NON_CRYSTALLISATION_TEXT` | 73 |

**`REFERENCE_ONLY` at 103 records is the surprise.** The fear that depositors would routinely
write "see publication" instead of a condition turns out to be unfounded at a rate of 0.05%.

### 2.3 The residual, and a small model to read it

**50,950 usable records carry at least one component the rule parser could quantify but not
name**: an amount with no reagent attached. A 32-billion-parameter teacher labelled a few thousand
of the hardest, and a 360M-parameter student (LoRA-tuned SmolLM2) learned from it and read the
residual in under two hours, contributing **35,580 components across 26,339 records** the rules
never named. A further 11,146 records are discarded outright under the codes above.

**Every component in the release carries a `parser` field**, `rules` or `slm`. Filtering to
`rules` recovers a fully deterministic subset with no model output, which is the only honest way
to ship a hybrid pipeline.

Against 192 hand-labelled records, reagent identity reaches **93.3% precision and 90.3% recall**,
against the rule parser alone at 95.1% and 69.5%.

### 2.4 Curation is most of the work

The reagent lexicon grew from 147 to 500 curated reagents carrying 1,587 spellings. Most of that
effort was not adding reagents but **deciding what is the same reagent**: PEG MME had seven
canonical identifiers for one molecule, and seven aliases were found to name a *different*
molecule entirely (barium against yttrium, CAPS against CHAPS, DTT against DTE).

## 3. What the data says

### 3.1 One record in six states an amount for nothing

**77,273 named components carry no concentration at all**, affecting 31,593 usable records, or
16.8%. A further large fraction of percentage concentrations carry no w/v or v/v marker: where the
parser inferred one it is flagged `unit_inferred`, and consumers should filter on that field
before treating a unit as reported fact.

### 3.2 Frequency reflects screen popularity, not efficacy

PEG 3350 leads the corpus at 45,202 conditions. It does not follow that PEG 3350 is the best
precipitant; it follows that PEG 3350 is in everybody's screens. 81,802 records match a
commercial screen well, and of those only a minority agree on every concentration, so most
deposited conditions are **optimised from a hit rather than the hit itself**.

### 3.3 The archive contains only successes

Every condition here produced a crystal. The database therefore supports
**P(condition | crystallised)** and says nothing about **P(crystallised | sequence)**. This is the
single most important limitation and no amount of data volume fixes it.

## 4. What can and cannot be learned from it

Three models were trained. The negative results are the more useful ones.

### 4.1 Construct boundaries: yes, to within nine residues

523,018 deposited chains each record which stretch of the full-length protein was cloned, mapped
back residue by residue through SIFTS. Those are free labels for the question *where do I cut?*

An ESM-2 t12-35M token classifier reaches **MCC 0.669 and a median boundary error of 9 residues**
on 4,077 held-out proteins split at 30% identity. Asked cold for hen lysozyme it proposes residues
19 to 147, exactly the mature chain after the signal peptide, having never been told what a signal
peptide is.

**The error distribution matters more than the median**: half the boundaries land within 5
residues, but the 90th percentile is 250. The model is excellent on most proteins and badly wrong
on a minority, and it half-knows which is which (mean predicted probability 0.97 on good
predictions against 0.82 on bad).

**This model resisted five separate attempts to improve it**: six structural features (secondary
structure propensity, ESMFold pLDDT, Pfam domain edges, disorder prediction, disorder as a
retrained input channel, surface entropy), soft training targets in two forms, doubling the
training schedule, and a fourfold larger backbone. None beat the original. Two of the features
carry demonstrably independent signal and still fail to help, which locates the ceiling in the
labels rather than the model: where a crystallographer cuts is partly convention, partly whichever
vector was to hand, and partly arbitrary.

### 4.2 Crystallisation propensity: weakly, and only in rank order

TargetTrack, the archived record of structural genomics targets, is the only substantial source of
crystallisation *failures*. 21,173 targets reached crystallisation; 79,926 reached purified protein
and never did.

**Conditioning the negatives on having reached purified protein is the load-bearing decision.**
Every target in the dataset got far enough that crystallisation was actually attempted, so the
model answers "given soluble purified protein, will it crystallise". Using all 335,771 targets
instead drops the positive rate from 20.9% to 6.3%, and a model trained that way predicts mostly
whether a gene expresses, which is a different and much easier question.

Gradient boosting on 30 sequence descriptors gives **AUC 0.656** on a 30%-identity cluster split,
barely clearing the 0.65 threshold declared before the run. The rank order is more useful than the
AUC: **the top 5% of ranked constructs crystallise 53% of the time against a 22% base rate.**
Sequence length, net charge and cysteine content dominate; disorder predictions add nothing
(AUC 0.654 to 0.656).

TargetTrack ends in 2017 and is dominated by structural genomics centres that chose tractable
targets. The figure is honest for that population and is not a universal probability.

### 4.3 Condition recommendation: no

*(Result pending: the full-scale run is in progress. The 3,000-record pass gave hit@5 of 0.848
against the frequency prior's 0.847, a margin of +0.001 against a pre-declared requirement of
+0.03, and was worse at hit@1 and hit@10. This section will state the full-scale figure.)*

The kill criterion was fixed before the experiment: beat a query-independent frequency prior at
L2 hit@5 by at least three percentage points, or publish the negative result without tuning.

This is consistent with an earlier finding that reshaped the project's plan. **Homology retrieval
also loses to the same frequency prior**, at every level, on both split thresholds, under three
definitions of ground truth. Fixing two evaluation flaws that had favoured the prior, adding a
homology-then-prior backoff, and repairing a split leak all failed to reverse it, and removing the
leak made homology *worse*.

The interpretation follows from section 3.2: condition frequency reflects screen popularity rather
than protein identity. Homologues were screened with the same popular screens as everything else,
so there is little sequence-specific signal to retrieve. **The archive supports choosing a screen
by precedent, but not by protein.**

## 5. Limitations

1. **Only successes.** No failed conditions, so no P(crystallised | condition).
2. **Frequency is popularity.** Common here means commonly tried, not commonly successful.
3. **Optimised, not screened.** Most matched conditions differ from the screen well they resemble.
4. **Unit inference is doing real work.** About 80% of percentage concentrations carry no w/v or
   v/v marker; inferred units are flagged, not hidden.
5. **Depositor errors are reproduced, not corrected.** A handful of records state nanomolar
   concentrations of bulk reagents, almost certainly millimolar typos. Silently fixing them would
   be a worse failure than passing them through.
6. **Protein concentration, drop ratio and equilibration volume are usually missing**, and they
   matter.
7. **The propensity model's negatives are era-limited** to pre-2017 structural genomics targets.

## 6. Availability

| | |
|---|---|
| Dataset | [10.5281/zenodo.21807134](https://doi.org/10.5281/zenodo.21807134), CC-BY-4.0 |
| Mirror | [`Dellboy/toppdblx-conditions`](https://huggingface.co/datasets/Dellboy/toppdblx-conditions) |
| Residual parser | [`Dellboy/toppdblx-residual-parser`](https://huggingface.co/Dellboy/toppdblx-residual-parser) |
| Boundary model | [`Dellboy/toppdblx-construct-boundary`](https://huggingface.co/Dellboy/toppdblx-construct-boundary) |
| Code | [bellcheddar/TopPDBLX](https://github.com/bellcheddar/TopPDBLX), MIT |
| Explorer | [toppdblx.mdeller.com](https://toppdblx.mdeller.com) |

Every pipeline stage writes a manifest of input hashes, tool versions and git state, so any figure
in this paper can be traced to the run that produced it.

Attribution to TopPDBLX does not discharge the obligation to the sources it derives from: the
Protein Data Bank (CC0), SIFTS, UniProt and TargetTrack.
