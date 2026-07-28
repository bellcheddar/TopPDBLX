# Crystal Ball: PDB Crystallisation Condition Normalisation and Sequence-Linked Prediction

**Project brief for planning. Author: Marc C. Deller, D.Phil.**
**Status: pre-implementation. This document is input to a planning pass, not a finished plan.**

---

## 1. Objective

Parse, normalise and curate **every crystallisation condition in the Protein Data Bank**, assign each one to a curated condition group, and link it to the sequence of the construct that produced it.

The end product takes an input amino acid sequence and returns:

1. **Matched entries**: homologous PDB entries and the conditions that crystallised them.
2. **Ranked condition recommendations**, expressed as curated groups anchored to real, orderable screen wells.
3. **Suggested constructs**: recommended N and C terminal boundaries and internal deletions, informed by predicted secondary structure, disorder, coiled-coil and low-complexity regions, plus empirical boundaries mined from the PDB itself.
4. **A crystallisation propensity estimate** for the input sequence and for each proposed construct.

Rationale: the strongest lever on crystallisation success is construct design, and the second strongest is choosing a screen that suits the protein. Both are currently done by intuition and precedent. All the precedent is sitting in the PDB in unstructured text.

---

## 2. Deliverables, phased

Each phase must stand alone and be publishable or usable on its own.

| Phase | Deliverable | Rough effort |
|-------|-------------|--------------|
| **0** | Cleaned, normalised, sequence-linked crystallisation condition database covering the whole PDB, released openly | 4 to 6 weeks |
| **1** | Curated condition ontology, versioned, with assignment logic and commercial screen cross-references | 2 weeks |
| **2** | Homology retrieval recommender plus browser front end | 2 weeks |
| **3** | Learned condition recommender, construct designer, propensity model | open ended |

Phase 0 is the value proposition. It is a dataset paper and a resource other people will use even if nothing downstream is ever built. **Do not build phase 3 first**: without phases 0 to 2 there is no evaluation that can be trusted.

---

## 3. Prior art to read before writing code

- **XtalPred** (Slabinski and Godzik, JCSG). Sequence features to crystallisation class: pI, insertion score, longest disordered region, coiled-coil content, low complexity, TM segments. This is the closest existing thing to the construct and propensity half.
- **Peat, Christopher and Newman**, on mining the PDB for crystallisation information. The ingest precedent.
- **Fazio, Peat and Newman**, on the non-random sampling of crystallisation space. Establishes the survivorship and screen-composition bias problem.
- **Newman's C6 database** of commercial screen formulations. Relevant to the cross-referencing step.
- **BMCD** (Biological Macromolecular Crystallization Database). Legacy, small, but the historical baseline.
- Crystallisation propensity predictors trained on TargetTrack: **ParCrys, OB-Score, XANNpred, CRYSTALP2, PredPPCrys, DeepCrystal, BCrystal, CLPred**. Know what each used as features and what accuracy they claimed, since these are the numbers to beat.
- **JCSG Top96** screen formulation (Deller, 2013), the seed taxonomy for this project. Seven precipitant classes: Organic, Organic/PEG/Salt, Organic/PEG, Organic/Salt, PEG, Salt, Salt/PEG.

---

## 4. Data sources

| Source | Use | Notes |
|--------|-----|-------|
| PDB mmCIF (full archive, rsync from RCSB or PDBe) | `_exptl_crystal_grow.*`, `_entity_poly.*`, `_struct_ref.*` | ~180k X-ray entries. Partially structured already: `.method`, `.pH`, `.temp` are separate fields, only `.pdbx_details` is free text |
| SIFTS | PDB chain to UniProt residue-level mapping | Essential for the empirical boundary work |
| UniProt | Full-length reference sequences | The "what was trimmed off" denominator |
| **TargetTrack** (archived PSI/PepcDB target status) | **Negative examples** | The only real source of failed targets. Without it there is no propensity model, only a conditional-on-success model |
| AlphaFold DB | pLDDT and PAE per residue | Where an entry exists; ESMFold locally where it does not |
| Commercial screen formulations | Cross-reference and parse validation | Hampton Crystal Screen, Index, PEGRx, SaltRx; Molecular Dimensions JCSG-plus, PACT, Morpheus; Qiagen JCSG Core; Rigaku Wizard |

---

## 5. Stage 1: ingest and parse

### 5.1 Scope

Realistic yield is 120,000 to 140,000 usable records from roughly 180,000 X-ray entries, after discarding `see publication`, bare `PEG`, empties and unparseable fragments. Track the discard reason for every rejected entry: the discard statistics are themselves a result worth reporting.

### 5.2 The hard cases

A rule-based parser plateaus around 75%. The residual is where a fine-tuned small language model earns its place. Known nasty patterns:

- v/v versus w/v ambiguity on PEGs and organics
- PEG molecular weight written as `3350`, `3.35K`, `PEG MME 2000`, `PEG monomethyl ether 550`
- Buffer pH versus final pH of the mixed solution (the JCSG Top96 document flags several of these explicitly)
- Ranges: `18-22% PEG 3350`
- Whole optimisation trays listed in one string
- Additives, cryoprotectants and seeding conditions mixed into the same field
- Non-English decimal separators and unicode fractions

### 5.3 Approach

1. Write the deterministic parser first. Regex plus a reagent synonym dictionary.
2. Use it to bootstrap labels; hand-correct 3,000 to 5,000 records, prioritising the ones the parser flags low-confidence.
3. Fine-tune **SmolLM2-135M or 360M** for text to schema-valid JSON. Constrained output, small vocabulary, so a tiny model is genuinely sufficient. Minutes to train.
4. Iterate twice: parse the full archive, sample the disagreements between rule parser and model, correct, retrain.

### 5.4 Critical distinction to capture

Many entries report the **optimised** condition, not the original screen hit. These two populations have different statistics. Add a boolean flag distinguishing "matches a known commercial screen formulation" (likely a screen hit) from "hand-mixed, non-standard concentrations" (likely optimised). Downstream models should be able to train on either or both.

### 5.5 Proposed record schema

```json
{
  "pdb_id": "1ABC",
  "entry_version": "2024-01-01",
  "raw_details": "...verbatim pdbx_details string...",
  "method": "VAPOR DIFFUSION, SITTING DROP",
  "temperature_k": 293,
  "ph": 7.5,
  "ph_source": "buffer | final | unstated",
  "components": [
    {
      "role": "precipitant | salt | buffer | additive | cryo | protein | unknown",
      "name_raw": "PEG 3,350",
      "name_canonical": "PEG_3350",
      "chem_class": "peg | organic | salt | buffer | polyol | detergent | other",
      "peg_mw": 3350,
      "concentration": 20.0,
      "unit": "percent_w_v | percent_v_v | molar | millimolar | mg_ml",
      "concentration_is_range": false,
      "concentration_range": [null, null]
    }
  ],
  "curated_group": {
    "l1_precipitant_class": "Salt/PEG",
    "l2_subclass": "peg_3350_plus_divalent_salt",
    "l3_group_id": "TOP96_F04",
    "assignment_distance": 0.08,
    "assignment_confidence": "high | medium | low | unassigned"
  },
  "commercial_screen_match": {
    "screen": "Hampton PEG/Ion",
    "well": "A7",
    "exact": true
  },
  "provenance": {
    "parser": "rules_v3 | slm_v2 | manual",
    "parse_confidence": 0.94,
    "flags": ["optimised_not_screen", "multiple_conditions_in_field"]
  },
  "ontology_version": "1.0.0"
}
```

Fix this schema before writing anything downstream. Every later stage inherits it.

---

## 6. Stage 2: curated condition ontology

### 6.1 Curated, not emergent

Groups are **hand-defined and human-readable**, then every parsed condition is assigned to its nearest group. Emergent clustering alone produces chemically meaningless groups and unorderable output.

However, clustering is still used, as a **diagnostic**: cluster the parsed conditions, find the clusters with no curated home, and add curated groups to cover the large orphans. The result is a human-defined ontology derived from the full data, which is defensible in a paper.

### 6.2 Three levels

- **L1, precipitant class.** The seven JCSG Top96 classes. Small, chemically coherent, learnable.
- **L2, subclass.** Roughly 30 groups. The fallback level when a sequence family has thin support.
- **L3, condition group.** Roughly 96 to 150 groups, each anchored where possible to a Top96 well, a JCSG Core Suite position, or a commercial screen well.

Predict at whichever level has adequate support. The distribution will be savagely long-tailed: PEG 3350 plus a salt will swallow a large fraction of everything.

### 6.3 Assignment function: the chemistry that matters

This is the technical crux. Encode the following correctly or the groups fragment for no chemical reason:

- **PEG molecular weight is ordinal, not categorical.** Encode on a log scale. PEG 3350 and PEG 4000 are near-interchangeable; PEG 400 and PEG 8000 are different reagents (one behaves as an organic, one as a true polymer precipitant).
- **Collapse buffer identity to pH.** At 0.1 M the buffer mostly just sets pH. Precedent from the Top96 itself: the Qiagen reformulation swapped CAPS for glycine at the same pH and the condition is still treated as equivalent. Treating buffer identity as distinguishing will shatter the ontology.
- **Normalise concentration distance per precipitant type.** 20% versus 25% PEG is a small step; 0.8 M versus 2.4 M ammonium sulfate is a different regime entirely.
- **Salt identity by Hofmeister position**, not string match. Sulfate, phosphate and citrate cluster; thiocyanate and nitrate sit apart.
- **pH banded at roughly one unit**, carrying the buffer-pH versus final-pH distinction as a separate field.

### 6.4 Multi-component screens

Morpheus and PACT style conditions carry a carboxylic acid mix, an alcohol mix and a buffer system simultaneously. They do not fit a seven-class precipitant taxonomy. **Decide before assignment begins**: either give them a top-level class of their own, or accept a `mixed_system` bucket. Do not leave this to be improvised per entry.

### 6.5 Commercial screen cross-reference

Match every parsed condition against published formulations of the major commercial screens. Two payoffs:

1. **Output becomes orderable.** "Maps to PACT E6, already in the fridge" beats "PEG 3350 at 18.4%, pH 6.9".
2. **Free parse validation.** An exact match to a known screen well is strong evidence the parse is correct. This yields a large automatic validation set with no hand-labelling.

### 6.6 Curation effort control

Do not hand-curate 130,000 entries. Hand-curate the roughly 100 to 150 **group definitions** and the **assignment rules**, then hand-audit a stratified sample of 1,000 to 2,000 assignments to get a real per-class accuracy number. That is a weekend of work, not a year.

**Version the ontology semantically and publish a changelog.** Every model trained afterwards is tied to a specific ontology version and must record it.

---

## 7. Stage 3: sequence linkage and redundancy control

### 7.1 Linking

`_entity_poly.pdbx_seq_one_letter_code` gives the construct sequence as crystallised, including tags. SIFTS maps to UniProt. Keep both: the construct sequence is what actually crystallised, the UniProt sequence is the full-length reference against which trimming is measured.

### 7.2 Complexes

For multi-entity crystals, decide once and apply consistently. Recommended: label the condition with the **largest polymer entity** and carry an `is_complex` flag plus the full entity list. Do not duplicate the record per chain.

### 7.3 Redundancy will destroy the metrics if ignored

Lysozyme, trypsin and their variants contribute thousands of entries; the same protein appears in the same condition hundreds of times.

1. Cluster all crystallised sequences with **MMseqs2** at 30% and 50% identity.
2. Deduplicate at the (sequence cluster, condition group) level so one heavily-studied protein cannot dominate.
3. **Split train, validation and test by sequence cluster, never by entry.**
4. Report against a **homology retrieval baseline**: for a query, return the conditions that worked for its closest PDB relatives. This baseline will be strong. If the learned model does not beat it, that is a cheap and useful negative result.

---

## 8. Stage 4: construct design features

Two independent signal sources, deliberately complementary.

### 8.1 Predicted, from sequence alone

| Feature | Tool | Note |
|---------|------|------|
| Disorder | metapredict, IUPred3 | metapredict is fast and pip-installable, which matters when scoring 200k sequences |
| Coiled-coil | DeepCoil | |
| Signal peptide | SignalP 6 | |
| Transmembrane | DeepTMHMM | |
| Low complexity | SEG | |
| Secondary structure | NetSurfP-3.0 or S4PRED | |
| Confidence and domain boundaries | AlphaFold DB pLDDT and PAE, else ESMFold locally | Low pLDDT plus high PAE at a terminus is the strongest trim signal |
| Physicochemical | pI, GRAVY, composition | XtalPred feature parity |

### 8.2 Empirical, from the PDB

Align every SEQRES against its SIFTS-mapped UniProt full-length sequence. This yields, at no labelling cost, tens of thousands of examples of **where successful crystallographers actually cut**. This is a real boundary prior and it encodes what pLDDT does not: people trim past predicted-ordered regions routinely, for expression and proteolysis reasons.

Train **ESM-2 t12-35M as a token classifier**: input full-length sequence, output per-residue probability of being inside a crystallised construct. Then propose boundaries by thresholding and smoothing, subject to sensible constraints (minimum construct length, do not cut mid-helix, do not cut inside a predicted domain).

---

## 9. Stage 5: model stack

| Task | Model | Why |
|------|-------|-----|
| Free text to JSON | SmolLM2-135M or 360M, fine-tuned | Constrained output, tiny vocabulary, minutes to train |
| Sequence to condition group | Frozen ESM-2 embeddings plus shallow multi-label head | Not an LLM fine-tune, much faster. Must be **multi-label**: proteins often crystallise in several unrelated conditions |
| Sequence to construct boundaries | ESM-2 t12-35M token classification | See 8.2 |
| Crystallisation propensity | Gradient boosting on XtalPred-style features plus embeddings, trained on TargetTrack | Boring, but will outperform a small neural net at this data volume |

---

## 10. Evaluation protocol

- All splits by **sequence cluster**, at both 30% and 50% identity thresholds. Report both.
- Condition recommendation: top-1, top-5 and top-10 accuracy at L1, L2 and L3. Compare against (a) the homology retrieval baseline, (b) a frequency prior that always returns the most common conditions. Beating the frequency prior is the minimum bar; beating homology retrieval is the real bar.
- Construct boundaries: per-residue MCC, plus boundary distance in residues against held-out real constructs.
- Propensity: AUC and precision-recall on TargetTrack held-out targets, with an explicit statement of which target-status stage counts as positive.
- Parse quality: accuracy on the hand-audited stratified sample, plus agreement rate with exact commercial screen matches.

---

## 11. Known biases and limitations, to state explicitly in any output

1. **The PDB contains only successes.** It supports P(condition | crystallised) but says nothing about P(crystallised | sequence). The propensity half depends entirely on TargetTrack negatives.
2. **Condition frequency reflects screen popularity, not intrinsic success rate.** PEG/Ion and PEG 3350 dominate because they are in everyone's screens. The Top96 carries the same caveat by construction.
3. **Reported conditions are often optimised, not screen hits.** Flagged in the schema, but it remains a confounder.
4. **Protein concentration, drop ratio and equilibration volume are usually missing**, and they matter.
5. **TargetTrack is archived and stops in the mid 2010s.** The negative set is therefore era-limited and skewed towards structural genomics targets.
6. Any recommendation is a prior over screening space, not a prediction of a specific hit.

---

## 12. Open decisions the plan should resolve

1. Final number of L2 and L3 groups, and the orphan-cluster threshold that triggers creating a new group.
2. Morpheus and PACT handling: separate top-level class, or `mixed_system` bucket.
3. Whether cryoprotectant composition is part of the condition group or a separate parallel field. Recommendation: separate, since the Top96 already treats cryo separately.
4. Multi-label ground truth construction: does a protein's label set include conditions from all its PDB entries, all its 90% identity cluster, or all its 50% cluster.
5. Which TargetTrack status transition defines a positive: crystallised, diffracted, or deposited.
6. Whether to include cryo-EM and NMR entries anywhere in the pipeline. Recommendation: exclude from conditions, retain for the construct boundary work, since a construct that behaved for cryo-EM is still evidence about boundaries.
7. Licensing and hosting for the released database.

---

## 13. Suggested repository structure

```
crystal_ball/
  data/
    raw/            # mmCIF mirror, SIFTS, UniProt, TargetTrack dumps
    interim/        # parsed but unassigned records
    processed/      # final linked database
  ontology/
    groups.yaml     # curated group definitions, versioned
    synonyms.yaml   # reagent name normalisation dictionary
    screens/        # commercial screen formulations
    CHANGELOG.md
  src/
    ingest/         # mmCIF harvest, field extraction
    parse/          # rule parser, SLM inference, reconciliation
    assign/         # distance function, group assignment
    link/           # SIFTS, UniProt, MMseqs2 clustering
    features/       # disorder, coiled-coil, SS, pLDDT harvest
    models/         # training and inference
    eval/           # split generation, metrics, baselines
  notebooks/        # exploratory only, nothing load-bearing
  app/              # single-file HTML front end
  tests/
```

---

## 14. Build constraints and preferences

- **Front end**: single-file HTML, CSS and vanilla JavaScript. Plotly.js for charts, Tabulator for tables. No React, Vue, npm, webpack, Flask, Dash or Streamlit. Mobile responsive. marcdeller.com brand theme.
- **Back end and pipeline**: Python. gemmi for mmCIF, Biopython where convenient, MMseqs2 shelled out, HuggingFace transformers for the models.
- **British English throughout.** No em dashes: use colons or parentheses.
- Everything reproducible from a single command per stage. Every stage writes a manifest recording input hashes, tool versions and ontology version.

---

## 15. First task for the planning pass

Produce an implementation plan for **Phase 0 only**, ending at a released, sequence-linked, parsed condition database. Include a task breakdown, a dependency order, the concrete Python packages per stage, and an estimate of where the hand-curation time is spent. Flag anything in section 12 that blocks Phase 0 as opposed to blocking later phases.
