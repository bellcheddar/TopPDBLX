# Ontology changelog

Spec 6.6 requires the ontology to be versioned semantically with a changelog, because every
model trained afterwards is tied to a specific ontology version and must record it.

Two artefacts are versioned here, independently:

| File | Version | What it is |
|------|---------|------------|
| `synonyms.yaml` | 0.8.7 | Reagent lexicon: canonical ids, aliases, PEG molecular weights, Hofmeister ranks, buffer pKas |
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

### 0.8.7 (2026-08-03)

**486 reagents, 1,487 names.** Two abbreviations from the head of the unidentified list that are
plainly real: `ams` → `AMMONIUM_SULFATE` (46 components) and `btprop` → `BIS_TRIS_PROPANE` (38).

Shipped alongside a `parse.noncomponent` change rather than a lexicon one, because the rest of
that list was narrative rather than chemistry. See the commit for the 2,416 components moved out
of the reagent denominator.

### 0.8.6 (2026-08-03)

**486 reagents, 1,484 names.** Repairs a regression this afternoon's own fix created, and adds
the head of the unidentified list.

**0.8.4 stripped `chaps` and `capso` from `CAPS` and `mopso` from `MOPS` as aliases naming a
different molecule, which was right, and then did not give those molecules an entry — which was
not.** 245 components stopped resolving. `CHAPS`, `CAPSO` and `MOPSO` now exist. Removing a wrong
alias without rehoming the reagent trades a wrong answer for no answer, and 0.8.4 got that right
for praseodymium, cobalt and NAG while missing it here.

**A sixth wrong alias, caught by the validator.** Adding `DTE` collided with `DTT`, which claimed
`dithioerythritol`. Dithiothreitol and dithioerythritol are **diastereomers** — different
molecules, both used as reducing agents in this corpus. Separated. This one is organic, so the
metal-and-anion test added in 0.8.4 cannot see it; only the uniqueness validator could.

**Aliases for the head of the unidentified list**, each a spelling of something already present:
`na3citrate` → `SODIUM_CITRATE`; `di-ammonium hydrogen citrate` → `DIAMMONIUM_CITRATE`; `acona`
(AcONa) → `SODIUM_ACETATE`; `sodium bicine` → `BICINE`; `deg` → `DIETHYLENE_GLYCOL`; `kpi` (KPi)
→ `POTASSIUM_PHOSPHATE`.

**Identification 85.5% → 85.7%**, and `unidentified_reagent` as a blocking reason falls 821 → 776.

**The rest of that list is not worth curating.** 30,978 distinct unidentified spellings, and the
top 200 cover only 13.4% of occurrences. What remains in the head is either genuinely ambiguous
(`peg`, `propanediol`, `phosphate` with no qualifier), a vendor stock the screens work would
resolve better (`NPS`, `divalents`), or method text the non-component classifier should be
catching rather than the lexicon (`results`, `days`, `vapor`, `hanging`, `if needed`) — roughly
600 components of that last kind, which is a parser fix and not a curation one.

### 0.8.5 (2026-08-03)

**482 reagents, 1,464 names.** Two more from Marc, and the general patterns behind them, both now
tested.

`NA_THIOCYANATE` → `SODIUM_THIOCYANATE`, and `BETA_MERCAPTOETHANOL_AT_298K` → BME. The second is
a reagent with a **temperature** welded to its name; sweeping for that pattern found three more:
`GLYCEROL_FOR_CRYOPROTECTION` (a purpose), `MPD_IN_RESEVOIR` (a vessel, and a typo), and two that
are not reagents at all — `PHOSPHATE_AT_PH_4_5`, which names an anion class rather than a salt,
the same decision as `carboxylic acid` in 0.5.1, and `INHIBITOR_TO_PEPTIDE`, which is a drop ratio.

Sweeping for the first pattern — an element **abbreviation** where the name belongs — found only
`NA_THIOCYANATE`, because 0.8.3 had already done the formula-shaped ids. Both sweeps are now
tests: `test_no_canonical_id_carries_method_or_temperature_text` and
`test_ids_spell_the_element_out_rather_than_abbreviating_it`.

### 0.8.4 (2026-08-03)

**488 reagents, 1,466 names.** A deliberate sweep for the barium/yttrium fault — an alias naming a
different molecule — after that one turned up by accident. **Four more found**, and the sweep is
now a test.

| entry | alias removed | why | components on the entry |
|---|---|---|---|
| `SODIUM_ACETATE` | `praseodymium acetate` | a different metal | 20,593 |
| `CALCIUM_ACETATE` | `cobalt acetate` | a different metal | 3,276 |
| `BETA_OG` | `n-acetyl-d-glucosamine` | NAG is a sugar, beta-OG is a detergent | 477 |
| `TRIS` | `bistris-hcl` | Bis-Tris is a different buffer, and `BIS_TRIS` already existed | 32,981 |

`bistris-hcl` moved to `BIS_TRIS` rather than deleted. The other three name reagents with no
entry of their own, so `PRASEODYMIUM_ACETATE`, `COBALT_ACETATE` and `N_ACETYL_GLUCOSAMINE` are
new: removing an alias without rehoming the reagent would trade a wrong answer for no answer.

**Three sweeps were run; only one was productive.** Comparing metals and anions between an alias
and its own entry found all four. Comparing *digits* produced 86 candidates and no faults —
`BUTANEDIOL_12` ← `1,2-butanediol` is right, the id merely compresses the locants. The 0.5.1
no-shared-word test produced 94, almost all legitimate acronym mappings: `ATP` ←
`adenosine triphosphate`, `MPD` ← `2-methyl-2,4-pentanediol`, `HEPES` ←
`4-(2-hydroxyethyl)-1-piperazineethanesulfonic acid`. It found the `BETA_OG` and `TRIS` errors
among them, so it earns its keep as a review aid rather than as a test.

**The metal and anion comparison is now `test_no_alias_names_a_different_metal_or_anion`.** It
covers the inorganic half of the fault class mechanically. The organic half — two entries naming
the same molecule in unrelated words — still needs a chemist, which is what `LEXICON_REVIEW.csv`
is generated for.

**Two left for human judgement**, flagged rather than changed: `HYDROXYMERCURYBENZOATE` claims
`para-chloromercuribenzoic acid`, and PCMB is strictly the chloro compound rather than the
hydroxy one; `SODIUM_DIHYDROGEN_PHOSPHATE` claims `k/nah2po4`, which names a potassium salt too.

### 0.8.3 (2026-08-03)

**485 reagents, 1,461 names.** Marc's review of `LEXICON_REVIEW.csv`, plus the general rule it
established, plus three alias errors that surfaced while applying it.

**Nine merges from the review**, all confirmed against the entries before applying:
`CARBOXYLIC_ACIDS_MIX` → `CARBOXYLIC_ACID_MIX` (a plural); `P8K` → `PEG_8000` (the Morpheus
spelling); `POLYVINYLPYRROLIDONE_K15` → `POLYVINYLPYRROLIDONE`; `CUCL2` → `COPPER_CHLORIDE`;
`CUSO4` → `COPPER_SULFATE`; `HEXAAMMINECOBALT_CHLORIDE` and `HEXAMINE_COBALT_CHLORIDE` →
`COBALT_HEXAMMINE`; `MNSO4` → `MANGANESE_SULFATE`; `NISO4` → `NICKEL_SULFATE`. Dropped:
`RECONSTITUTED_IN_DMSO` and `CADMIUM_SULFATE_12_20MM`.

**One item was corrected rather than applied.** The review asked to combine
`N_DECYL_BETA_D_MALTOSIDE` with `N_DODECYL_BETA_D_MALTOSIDE`. Those are different detergents —
decyl is C10, dodecyl is C12, DM and DDM — and merging them would have repeated the 0.5.0 fault
of aliasing one molecule to another. But the observation was right that this family was
duplicated: `N_DECYL_BETA_D_MALTOSIDE` duplicated `DECYLMALTOSIDE`, and
`N_DODECYL_BETA_D_MALTOSIDE` duplicated `DDM`. Merged along the correct axis, and the two
survivors now carry chemical display names rather than abbreviations.

**"Use the name, not the formula" is now applied throughout.** `HCL`, `NAOH`, `CH3COONA`,
`CH3COONH4`, `NAKPO4`, `NA2SO3`, `NAHCO3`, `H2O`, `HGCL2`, `RBCL` and `YBCL3` duplicated
name-based entries and were folded into them. `GDCL3`, `YCL3`, `BECL2`, `ALCL3` and `LIBR` had no
name-based entry and were renamed in place — `GADOLINIUM_CHLORIDE`, `YTTRIUM_CHLORIDE`,
`BERYLLIUM_CHLORIDE`, `ALUMINIUM_CHLORIDE`, `LITHIUM_BROMIDE` — with the formula kept as an alias,
which is the right way round: the formula is a spelling, the name is the identity.

**Three aliases named a different molecule**, the fault class of 0.5.0, found while scanning the
formula-shaped ids:

| entry | alias removed | why |
|---|---|---|
| `CAPS` | `chaps` | CHAPS is a zwitterionic detergent, not a buffer |
| `CAPS` | `capso` | CAPSO differs by a hydroxyl |
| `MOPS` | `mopso` | MOPSO differs by a hydroxyl |

**And the uniqueness validator caught a fourth.** Renaming `YCL3` to `YTTRIUM_CHLORIDE` collided
with an alias `yttrium chloride` sitting on **`BARIUM_CHLORIDE`**. Barium is not yttrium. It had
resolved silently since it was added, which is exactly why that validator exists — this is the
fifth separate edit it has caught.

### 0.8.2 (2026-08-03)

**509 reagents, 1,462 names.** The last class the spelling tests cannot reach: a real reagent name
with an **amount or method word left attached** by the clause splitter. Found by scanning every
canonical id for a token that describes a quantity, a vessel or a procedure rather than a
molecule.

| merged | into | components |
|---|---|---|
| `SATURATED_AMMONIUM_SULPHATE`, `M_AMMONIUM_SULPHATE_1_7` | `AMMONIUM_SULFATE` | 61 |
| `VOL_VOL_GLYCEROL`, `CRYOPROTECTED_WITH_20_GLYCEROL` | `GLYCEROL` | 43 |
| `MG_CHLORIDE` | `MAGNESIUM_CHLORIDE` | 44 |
| `METHANOL_WATER_IN_RESERVOIR` | `METHANOL` | 71 |
| `BES_BUFFER` | `BES` | 95 |

`MG_ACETATE_5_MM_TCEP` dropped: it names two reagents, so no single id can be right.

**`MIB_BUFFER` and `MMT_BUFFER` are kept deliberately.** The scan flags them for containing
"buffer", but MIB (malonate / imidazole / borate) and MMT (DL-malic acid / MES / Tris) are
Molecular Dimensions buffer *systems* whose product name includes the word. 1,177 components
between them, and merging them into anything would be wrong.

**Fuzzy name matching was tried and mostly rejected.** Comparing every same-class pair by string
similarity produced 489 candidates, and requiring the digit sequences to agree — because numbers
are the chemistry, PEG 400 is not PEG 4000 — still left 120, nearly all false: `HEPES` against
`MES`, `BIS_TRIS` against `TRIS`. Chemical names share morphemes, so similarity is a weak signal.
It surfaced exactly one real fault, `SATURATED_AMMONIUM_SULPHATE`, and that one generalised into
the mechanical scan above, which found nine more with no false positives.

**What is left is beyond a script.** Two entries naming the same molecule in entirely different
words — `MPD` and `2-methyl-2,4-pentanediol` were one such pair — cannot be found by normalising
strings. `LEXICON_REVIEW.csv` ships all 509 entries with component counts and aliases, sorted by
class then name, for exactly that review.

### 0.8.1 (2026-08-03)

**517 reagents, 1,463 names.** The PEG monomethyl ether merge in 0.8.0 fixed one instance of a
general fault, so this looks for the rest of it mechanically. **36 duplicate ids merged into 18
canonical entries**, and four more clause artefacts dropped.

Five tests, each asserting that no two canonical ids can collide under a normalisation that
cannot change what a molecule is:

| test | what it catches |
|---|---|
| identical token multiset | `PEG_2000_MONOMETHYL_ETHER` / `MONOMETHYL_ETHER_PEG_2000` — word order |
| identical flattened id | `PEGMME2K` / `PEG_MME_2K`, `PEG_20_000` / `PEG_20000` — punctuation |
| tokens minus hydration and unit words | `PEG_3350_W_V` / `PEG_3350` — the splitter leaking a unit |
| same `peg_mw` and `is_mme` | the whole monomethyl ether family, in one query |
| identical display name | `LI_SULFATE` / `LI_SULFATE_2` |

Merged: `NH4ACETATE`, `NH4_ACETATE`, `NH4_ACETATE_2` → `AMMONIUM_ACETATE`; `LI_SULFATE`,
`LI_SULFATE_2` → `LITHIUM_SULFATE`; `COBALT_HEXAMINE`, `HEXAMMINECOBALT_CHLORIDE`,
`HEXAMMINE_COBALT_CHLORIDE` → `COBALT_HEXAMMINE`; `PEG_20_000` → `PEG_20000`; `PEG_10_000` →
`PEG_10000`; `POLYETHYLENE_GLYCOL_4K` → `PEG_4000`; `POLYETHYLENE_GLYCOL_8K` → `PEG_8000`; and
the `W_V`, `WT_VOL`, `PERCENT` and `SATURATED` variants of PEG 3350, 6000, 8000 and 400 onto
their real entries.

**Merged rather than deleted, deliberately.** `W_V_PEG3350` really does name PEG 3350 — the
splitter leaked the unit into the name — so deleting it would discard a correct identification
to punish a parser bug. Only ids naming *two or more* reagents were dropped:
`MPD_PEG1000_PEG3350`, `PEG_3350_0_1M_CACODYLATE`, `TRIS_HCL_24_PEG3350`, `UL_16_PEG3350`.

**Two chemistry errors fell out of the same sweep.** `PEG_1_5K` carried `peg_mw` **5000**, which
is why it grouped with PEG 5000; PEG 1.5K is PEG 1500, and it is now merged there. `PEG_33500`
claimed a molecular weight no vendor sells and is PEG 3350 with a trailing zero.

**Why it matters beyond tidiness.** Found while preparing round 09. A model trained to emit
canonical ids was being shown eight ids for one polymer, which is the one thing such a model must
not learn. The invariant is now tested, so a future entry cannot reopen it silently.

### 0.8.0 (2026-08-03)

**557 reagents, 1,467 names.** PEG monomethyl ether was fragmented across up to **seven canonical
ids per molecular weight**, every one carrying the same `peg_mw`:

| molecular weight | canonical entry | duplicates | components split off |
|---|---|---|---|
| 2000 | `PEG_MME_2000` (3,442) | 8 | 339 |
| 5000 | `PEG_MME_5000` (1,739) | 9 | 367 |
| 550 | `PEG_MME_550` (2,037) | 2 | 97 |
| 350 | `PEG_MME_350` (95) | 1 | 62 |

`MONOMETHYLETHER_PEG_2000`, `MONOMETHYL_ETHER_PEG_2000`, `PEG_2000_MONOMETHYLETHER`,
`PEG_2000_MONOMETHYL_ETHER`, `PEG_MONOMETHYLETHER_2000`, `PEG_MONOMETHYL_ETHER_2000`, `PEGMME2K`
and `PEG_MME_2K` are one molecule written eight ways. 20 duplicate entries merged, 20 spellings
folded in as aliases.

**Two harms, and the second is the one that mattered tonight.** Anyone querying PEG MME 2000 got
3,442 components instead of ~3,780, a 9% undercount. But the training targets were teaching the
model *eight different canonical ids for one reagent*, which is precisely the thing a model
emitting canonical ids must not learn. This was found while preparing round 09 and fixed before
it trained.

Same failure as the systematic-name duplicates merged in 0.7.0 — `MES` had four entries — and
found the same way, by grouping ids that share a property no two distinct molecules can share. In
0.7.0 that was a chemical name; here it is `peg_mw`.

**Two clause artefacts dropped:** `PEG_MME_550_PEG20000` names two reagents and
`PEG_MME_500_20` has a concentration in it. Neither is a molecule.

**Five entries added** from the blank-record teacher output, each verified against its deposition
text: `AMMONIUM_CACODYLATE`, `PYRIDINE`, `XYLOBIOSE`, `LEVULINIC_ACID`, `COPPER_ACETATE`. Plus
`methane pentanediol` on `MPD` and the bracketed spelling of ADA's systematic name.

**Punctuation-insensitive matching was measured and rejected.** Stripping all punctuation before
lookup would resolve 487 further model components, but it also maps `ETHYLENE_GLYCOL_PEG_8000` to
`PEG_8000` (losing half a Morpheus mix) and `PEG_3350_0` to `PEG_33500`. The gain is real and the
errors are silent, so the spellings worth having are added explicitly instead.

### 0.7.0 (2026-08-03)

**574 reagents, 1,456 names.** Minor rather than patch: entries are removed and merged, so a name
that resolved yesterday may resolve to something else today, or to nothing.

From ranking the 8,437 distinct names the SLM emitted that the lexicon does not know, by corpus
frequency, and reading the deposition text behind the top 120 rather than judging them by name.
That last part decided most of the outcomes, and reversed several.

**29 entries removed, because they were never reagents.** Corpus mining had promoted whole
clauses to canonical entries, each of which then resolved and so counted as a successful
identification:

    WELL_3_DROP_CRYSTALLIZATION_PLATE                                          41 components
    SATURATED_WITH_PARA_CHLOROMERCURIBENZOIC_ACID                              39
    CRYOPROTECTED_WITH_20_ETHYLENE_GLYCOL                                      36
    COMPOUND_STOCK_SOLUTIONS_EITHER_100MM_OR_1M_STOCKS_WERE_ADDED_UP_TO_...    31
    UL_OF_S_ADENOSYL_METHIONINE_IN_WATER_WERE_ADDED_TO_100UL_OF_PROTEIN        31
    BATCH_CRYSTALLIZATION_DONE_IN_POLYPROPYLENE_TUBES                          15

This is the failure mode the 0.5.1 entry named: a wrong entry *raises* the identification rate,
because the name resolves. The corpus looked better for being wrong.

**7 systematic names merged into the molecule they already name.** `MES` had four separate
entries -- `N_MORPHOLINO_ETHANESULFONIC_ACID` and three variants of its punctuation -- holding 190
components between them, `HEPES` had one, `ADA` two. Every duplicate was classed `additive` with
no `buffer_pka`, so a MES written systematically was not recognised as a buffer at all and could
not contribute its pH.

**42 reagents added.** PEG Smear High, Low and Medium (`PEG_SMEAR_BROAD` already shipped and set
the convention: `chem_class: other`, because a vendor PEG mixture has no single molecular weight);
PGA-LM and gamma-PGA; the detergents CYMAL-6, CYMAL-7, DDAO, UDAO, C11DAO, HEGA-10 and
Fos-Choline-9; the nucleotides dATP, dGTP, dUMP, CDP and IMP; L-cysteine, L-methionine,
L-tryptophan, L-phenylalanine, L-glutamic acid; PMSF, putrescine, monoolein, coenzyme A, folinic
acid, MTA, glucose-6-phosphate, glutaric acid; and the salts diammonium citrate, iron(II)
sulfate, manganese(II) acetate, caesium sulfate, caesium acetate, barium acetate and lithium
formate.

**20 aliases onto entries that already existed** -- `pvp` on `POLYVINYLPYRROLIDONE`, `tdp` on
`THIAMINE_DIPHOSPHATE`, `coh18n6` on `COBALT_HEXAMMINE`, `dho` on `DIHYDROOROTATE`, `mega 8` on
`MEGA8`, `dodecyl maltoside` on `DDM`.

**Reading the text reversed several decisions the name alone would have got wrong.** `L_CYSTINE`
is L-*cysteine*. `MAGNESIUM_8` is the detergent *Mega 8*. `FOLIC_ACID` is *folinic* acid, a
different molecule. `COACHINE_A` is *coenzyme A*. `PENTANEDIOL_12` is 2-methyl-2,4-pentanediol,
which is MPD. `PEG_2700` is a 27% concentration. `PEG_20` is `PEG 20, 000` split at its own comma.
None of these are lexicon gaps and adding them as written would have created five wrong entries.

**The uniqueness validator caught a sixth**, rejecting `2-hydroxyethyl disulfide` as already
claimed -- which is how `HYDROXYETHYL_DISULFIDE_2` turned out to exist. Merged rather than
duplicated. It has now earned its place in four separate edits.

### 0.6.2 (2026-08-03)

**562 reagents, 1,370 names.** Found by taking the 32B teacher's 47 false positives across both
gold sets and reading each one against its own deposition text, rather than by scanning the
lexicon against itself.

- **`HEXANEDIOL_25` gains four spacing variants** — `2,5 hexanediol`, `2,5-hexane diol`,
  `2,5-hexandiol`, `hexane-2,5-diol`. Only the fully hyphenated spelling was listed, and 28
  occurrences in the residual use another. This is the dangerous kind of gap rather than a merely
  wasteful one: `hexanediol` unqualified is an alias of `HEXANEDIOL_16`, which outnumbers the 2,5
  isomer 730 to 42, so a missed 2,5 spelling is not dropped but at risk of being read as the wrong
  molecule.
- **`BUTANOL_2` is new**, with `2-butanol`, `sec-butanol` and `butan-2-ol`. Nine occurrences in
  the residual, resolving to nothing.

**Two further changes were proposed and rejected on the evidence, which is the more useful
result.**

`TERT_BUTANOL` claims the bare alias `butanol`, which has the exact shape of the 0.5.x bugs — a
stem that names four isomers, aliased to one of them, and this corpus holds 340 tert against 219
1-butanol, so there is no dominant convention to appeal to. It was queued for removal. Reading the
25 residual occurrences of bare `butanol` first showed every one of them to be a `TERTIARY
BUTANOL` or `tert Butanol` whose qualifier the splitter had separated. The alias is not
overreaching, it is catching that split, and removing it would have cost correct identifications
to fix a bug that was not there.

A `2,4-pentanediol` gap was also queued, on 639 residual hits. Every one is
`2-methyl-2,4-pentanediol`, which is MPD, already an entry.

**Checking the text before editing changed two of four decisions.** A frequency scan says which
spellings exist; only the surrounding words say what they mean.

### 0.6.1 (2026-08-03)

**561 reagents, 1,362 names.** From the second gold batch, 96 records sampled where the 32B
teacher and the pipeline disagree rather than at random. Unresolvable gold labels fell from 14 to
6, and the six that remain are ambiguous abbreviations (`L-RHA`, `PEA`, `AVA`, `MG(II)`) or
one-off ligands, not gaps.

| New entry | Why |
|---|---|
| `NDSB_201` | A different sulfobetaine from `NDSB_195`: benzyl rather than ethyl |
| `ISOCITRATE` | |
| `UMP` | |
| `DICOUMAROL` | |
| `XYLOPENTAOSE` | |

Three labels turned out to name entries that already existed and became aliases: `glycyl-glycine`
→ `GLYCYLGLYCINE` (hyphenated, and only the closed spelling was listed), `octyl
d-beta-glucopyranoside` → `OG`, and `dimethylethylammonium propane sulfonate` →
`NDSB_195`, which is the same systematic name the 0.6.0 note already recorded, written without
the locant.

**Aliasing an existing entry is the commoner fix, and the one a gold set is needed to see.** Of
the eight labels resolved here, three were spellings rather than molecules. An unrecognised
spelling is invisible to every automated metric in the project: the name simply fails to resolve
and the component is dropped, which costs recall without costing any measurable precision.

### 0.6.0 (2026-08-02)

**556 reagents, 1,346 names.** From the first gold set — 96 records labelled by hand, the first
ground truth this project has had. Fourteen new entries, every one of them a reagent a human
labeller saw in the text and the lexicon had no name for.

`AMPD`, `GLYCYLGLYCINE`, `CALCIUM_NITRATE`, `NDSB_195`, `CYMAL_3`, `NONYL_GLUCOSIDE`,
`TETRAETHYLAMMONIUM_CHLORIDE`, `ALUMINIUM_FLUORIDE`, `RHODIUM_HEXAMINE`, `CHLOROTRYPTOPHAN_7`,
`DEOXYGUANOSINE`, `DEOXYCYTIDINE`, `SAH`, `STAUROSPORINE`.

Plus `AMMONIUM_SULFATE` ← `diammonium sulfate`/`diammonium sulphate`, and `GLUTATHIONE` ← `gsh`,
`gssg`, `gsh/gssg`, the last of which is a redox pair written as one token.

Minor rather than patch because new canonical ids appear: a name that resolved to nothing
yesterday resolves to a reagent today, and anything counting identified components moves.

### 0.5.1 (2026-08-02)

**542 reagents, 1,306 names.** Twelve more aliases naming a different molecule, found by scanning
every entry for a *multi-word* alias sharing no substantial word with its own name. The first pass
of that scan ranked by corpus frequency and surfaced only formulae (`nacl`, `mgcl2`) and
concatenations (`peg3350`), which are legitimate; restricting it to multi-word English names cut
246 candidates to 50 and made the real errors visible.

| Entry | Alias removed | Now resolves to | Records |
|---|---|---|---|
| `SODIUM_CACODYLATE` | `carboxylic acid` | nothing, correctly: it names a chemical class | 381 |
| `MALIC_ACID` | `malonic acid` | new `MALONIC_ACID` | 123 |
| `BENZAMIDINE` | `guanidine hydrochloride` | `GUANIDINE_HCL`, which already existed | 73 |
| `SODIUM_OXAMATE` | `sodium oxalate` | new `SODIUM_OXALATE` | 50 |
| `MALIC_ACID` | `maleic acid` | new `MALEIC_ACID` | 42 |
| `SODIUM_SULFATE` | `sodium thiosulfate` | new `SODIUM_THIOSULFATE` | 30 |
| `DISODIUM_HYDROGEN_PHOSPHATE` | `sodium pyrophosphate` | new `SODIUM_PYROPHOSPHATE` | 29 |
| `TRIS` | `tcep hydrochloride` | `TCEP`, which already existed | 25 |
| `BENZAMIDINE` | `betaine hydrochloride` | `BETAINE`, which already existed | 25 |
| `SODIUM_SULFATE` | `sodium sulfite` | new `SODIUM_SULFITE` | 23 |
| `SODIUM_CITRATE` | `na nitrate` | `SODIUM_NITRATE`, which already existed | 19 |
| `MALIC_ACID` | `sodium molybdate` | new `SODIUM_MOLYBDATE` | 19 |

Plus `SPERMINE` ← `serine` and `SODIUM_CITRATE` ← `kcitrate`, both found the same way.

**`MALIC_ACID` had swallowed three unrelated reagents** — malonic acid, maleic acid and sodium
molybdate — and drops from 636 components to 636 minus the 87 that were never malic acid.
**`SPERMINE` had swallowed serine**, an amino acid aliased to a polyamine; `L_SERINE` already
existed, so the alias simply belonged there.

None of this was visible in any metric. A wrong alias *raises* the identification rate, because
the name resolves: the corpus looked better for being wrong. Identification moves 516,301 to
516,291, entirely from `carboxylic acid` correctly ceasing to resolve.

The uniqueness validator earned its place three times in one edit, rejecting `l-serine`,
`malonate` and `maleate` as already claimed — which is how `L_SERINE`, `SODIUM_MALONATE` and
`SODIUM_MALEATE` turned out to exist already.

### 0.5.0 (2026-08-01)

**535 reagents, 1,300 names.** Minor rather than patch, because this splits entries apart:
a name that resolved to one canonical id yesterday may resolve to a different one today.

Found by the second classification accuracy audit, from two notes — "NADP not NAD" (6FKU) and
"Propane is incorrect its a gas, missed propanediol" (1FDW). Chasing those turned up a family of
the same fault: **an alias naming a genuinely different molecule**, which silently merges two
reagents and makes both counts wrong.

| Entry | Aliases removed | Now resolve to | Records |
|---|---|---|---|
| `NAD` | `nadp`, `nadp+`, `nadh` | new `NADP`, new `NADH` | 462, 159 |
| `ETHYLENE_GLYCOL` | `diethylene glycol`, `tetraethyleneglycol`, `pentaethylene glycol`, `pentaethyleneglycol` | new `DIETHYLENE_GLYCOL`, `TETRAETHYLENE_GLYCOL`, `PENTAETHYLENE_GLYCOL` | 129, 119, 133 |
| `ETHYLENE_GLYCOL` | `hexylene glycol` | `MPD`, which it is another name for | 30 |
| `ETHYLENE_GLYCOL` | `1,2-butanediol` | new `BUTANEDIOL_12` | 19 |
| `PROPANEDIOL_13` | `triethylene glycol`, `tetraethylene glycol`, `triethyleneglycol` | new `TRIETHYLENE_GLYCOL`, `TETRAETHYLENE_GLYCOL` | 82, 77, 47 |
| `PROPANEDIOL_12` | `polypropylene glycol` | new `POLYPROPYLENE_GLYCOL` | 163 |
| `PEG_400` | `polypropylene glycol 400`, `polypropylene glycol p400` | `POLYPROPYLENE_GLYCOL_P_400` | 163 |
| `PEG_2000` | `polyethylene glycol 2000 mme` | `PEG_MME_2000`, which already existed | 1,298 |

**`POLYPROPYLENE_GLYCOL_P_400` already existed** as `chem_class: peg` with `peg_mw: 400`, while
`PEG_400` separately claimed two of its spellings — so the same reagent resolved two ways
depending on how the depositor wrote it. Both PPG entries are now `chem_class: organic` with no
`peg_mw`: PPG is a different polymer from PEG, and this **moves those conditions out of the PEG
family into Organic**, which is the correction, not a side effect.

Identification 516,241 → 516,301, rate 0.8526 → 0.8527. The gain is small because these names
mostly resolved before — to the wrong reagent.

### 0.4.1 (2026-08-01)

**527 reagents, 1,293 names.** Three additions, every one of them found by the first
classification accuracy audit rather than by inspecting the lexicon, which is the point: these
are misses the corpus actually produced.

| Added | Where | Why |
|---|---|---|
| `CHOLINE`, a new entry, `additive` | 3P03, "5 mM Choline" | The free cation, deposited bare and at additive concentrations. Deliberately its own entry rather than an alias of `CHOLINE_ACETATE` or `CHOLINE_DIHYDROGEN_PHOSPHATE`, which are specific ionic liquids from Hampton HR2-078/079 |
| `2-oxyglutarate` → `OXOGLUTARATE_2` | 3THP | Depositor spelling, oxy for oxo |
| `1,6-hexnediol` and `1,6 hexnediol` → `HEXANEDIOL_16` | 2ATB, "1M of 1,6 Hexnediol" | Depositor typo. The entry already carried `1,6-hexandiol` and `1,6-haxanediol`, so this is the established pattern |

Both separators are listed for the hexanediol typo because **nothing normalises a hyphen to a
space**: `tidy_name` does not, and the hyphenated forms that resolve today do so through
`display_name`, not through any alias rule. A single-separator alias covers one spelling only.

Note that the version table above read 0.2.0 while the file itself had reached 0.4.0: releases
0.3.0 and 0.4.0 were never logged here. That gap is not reconstructed, only stopped.

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
