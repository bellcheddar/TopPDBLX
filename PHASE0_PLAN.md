# Crystal Ball: Phase 0 Implementation Plan

**Scope: Phase 0 only.** Ends at a released, sequence-linked, parsed crystallisation condition
database covering the whole PDB. No ontology assignment, no recommender, no models beyond the
small language model needed to reach parse coverage.

**Input document:** `crystal_ball_spec_v1.md` (Deller, 2026-07-28).
**Plan version:** 1.1, 2026-07-28.
**Convention:** British English, no em dashes, one command per stage, manifest per stage.

**Decisions taken (2026-07-28):**

- **Ingest route:** build on the RCSB Data GraphQL API now; take one frozen 90 GB archive snapshot at
  release time and run the fidelity check at full scale against it. See 0.3.
- **Cryoprotectants (spec decision 12.3):** cryo reagents stay in `components[]` with `role: cryo`
  and a confidence; only **explicitly declared** cryoprotectants are excluded from the screen-matching
  fingerprint. See 0.6 and WP5.
- **Record key:** `(pdb_id, crystal_id)`, not `pdb_id`. See 0.7.

---

## 0. What changed after checking reality

Five findings from probing the live RCSB API and this machine before planning. Each one changes a
decision in the spec, so they are stated up front rather than buried.

### 0.1 The archive is bigger than the spec assumed

Live counts from the RCSB Search API on 2026-07-28:

| Query | Count |
|-------|-------|
| `exptl.method == X-RAY DIFFRACTION` | **205,949** |
| ... with `exptl_crystal_grow.pdbx_details` present | **198,432** |
| ... with `exptl_crystal_grow.temp` present | 179,297 |
| ... with `exptl_crystal_grow.method` present | 179,165 |
| ... with `exptl_crystal_grow.pH` present | 150,574 |
| All experimental entries (any method) | 256,789 |

The spec assumed ~180k X-ray entries and a realistic yield of 120k to 140k usable records. The pool
is 198k with a non-empty details string. On the evidence in 0.2, the yield target should be revised
**upwards to 150,000 to 170,000**, and any shortfall below that is a parser problem, not a data
problem.

### 0.2 The free text is shorter and cleaner than feared

A 600-entry probe stratified across four release-date slices (oldest, two middles, newest).

**Sampling caveat, stated once and applying to every percentage in section 0.** These probes draw
blocks of consecutive entries ordered by release date, so they are cluster samples, not simple
random samples. Entries released in the same week are highly correlated. Percentages below are
indicative of magnitude, not precise archive-wide rates, and each one should be recomputed exactly
during WP1 on the full ingest. Where an authoritative count exists from the Search API it is used in
preference: for example the true rate of X-ray entries lacking `pdbx_details` is **3.6%**
(7,517 of 205,949), not the higher figure a date-clustered sample suggests.

- Median `pdbx_details` length: **77 characters**. 90th percentile 161. Maximum 809.
- `see publication` and similar dodges: **0 of 600**. This pattern appears to be far rarer than the
  spec anticipates. Discard volume will be dominated by *empty* fields, not by evasive ones, and
  empties concentrate heavily in pre-1995 entries.
- Unicode outside ASCII: **0 of 600**. The non-English decimal separator and unicode fraction risk
  in spec 5.2 looks small. Still handle it, but do not budget days for it.
- Explicit `v/v`: 39 of 600. Explicit `w/v`: 75 of 600. So **roughly 80% of percentage
  concentrations carry no v/v or w/v marker at all**, and the defaulting rule by chemical class is
  therefore doing most of the work. This is the single highest-leverage rule in the parser.
- Ranges (`18-22%`): 30 of 600, about 5%.
- PEG monomethyl ether variants: 30 of 600, about 5%. Worth first-class handling.

Representative real strings, unedited:

```
0.1M Sodium acetate trihydrate (pH 4.6), \n 1.0M Ammonium sulfate
20 % w/v Polyethylene glycol 6,000  100 mM tri-Sodium citrate; pH 5.0
1.7 to 2.1 ammonium sulfate\n0.1M MES (pH 6 to 7)\n5 to 15%(v/v) glycerol\n7 to 12%(v/v) 1,4-dioxane
5 mM MnCl2 in 20 % PEG 3350, and 172 mM ammonium nitrate
100 mM Tris-HCl, pH 5.9-6.2, 100-140 mM ammonium phosphate dibasic, 180-200 mM sodium chloride, 34% PEG400
```

Note the third example: units omitted entirely on the ammonium sulfate (`1.7 to 2.1`, molar
implied), a pH range, and two separate v/v organics. That single record exercises five of the seven
hard cases in spec 5.2. Records like it are the reason the rule parser plateaus.

### 0.3 Build on the Data GraphQL API, snapshot the archive at release

**Decided.** The spec proposes rsyncing the full mmCIF archive up front. Measured against that,
with `rsync --stats` in dry run on 2026-07-28:

> `rsync.rcsb.org::ftp_data/structures/divided/mmCIF` is **90,004,014,513 bytes (90.0 GB) across
> 258,044 files.**

- **Disk.** 246 GB free of 3.6 TB (94% full). A 90 GB mirror leaves 156 GB, and it grows weekly.
- **iCloud eviction.** `Documents` is subject to macOS Optimize Mac Storage, which silently evicts
  large files. A 90 GB mirror inside `Documents` will be partially dataless within weeks.
- **Yield.** Phase 0 needs four fields per entry plus the polymer entities. Fetching 90 GB up front
  to read roughly 200 MB of text is a poor trade at the start of the project.

The adopted route takes the API for the build and the archive for the paper:

1. **Now (WP1):** ingest via GraphQL, gated on a 200-entry byte-comparison against mmCIF.
2. **At release (WP9):** take one frozen 90 GB archive snapshot and re-run the fidelity check
   **at full scale**, over all 198,432 entries with a details string.

The second step is what makes this better than the API alone. It converts the reproducibility
objection from a hand-wave into a measured claim in the dataset paper: agreement between the parsed
source field and the archive on every entry, as of a stated date, rather than on a 200-entry spot
check. It also means the 90 GB is needed in week 5, not week 1, and can be deleted afterwards once
its hash and the agreement report are recorded in the manifest.

The RCSB Data GraphQL endpoint returns all of it. Verified working against `data.rcsb.org/graphql`:
`exptl_crystal_grow { method temp pH pdbx_details pdbx_pH_range }`, `exptl { method }`,
`rcsb_accession_info { initial_release_date revision_date }`, and
`polymer_entities { entity_poly { ... } rcsb_polymer_entity_container_identifiers { uniprot_ids } }`,
batched at 300 identifiers per request. 206k entries is roughly 690 requests, one afternoon, a few
hundred MB of gzipped JSON kept verbatim as provenance.

**This is a fidelity claim and it must be tested, not assumed.** WP1 includes a hard gate: pull 200
random entries as mmCIF from `files.rcsb.org`, parse `_exptl_crystal_grow.pdbx_details` with gemmi,
and require byte-identical agreement with the API string. If the gate fails, fall back to the rsync
route with `data/raw` living **outside** `Documents` (recommend `~/CrystalBallData`, symlinked in).

### 0.4 Train the SLM with MLX-LM, not HuggingFace transformers

The spec says HuggingFace transformers. On this M1 Max, `torch` and `transformers` are not
installed, whereas MLX-LM is the proven fine-tuning path from chem_sage and chatPDB, with native
`--report-to wandb`, `--mask-prompt` and `mx.set_wired_limit()`. SmolLM2-360M under MLX-LM LoRA is a
minutes-scale job. Three standing hazards from previous rounds apply and are baked into WP6:
run `preflight.sh` before every launch, never disable `grad_checkpoint` to fit memory (it corrupts
output silently rather than erroring), and check for iCloud-evicted dataless files before each run.

### 0.5 Environment is close to ready

Python 3.14.3 is the house interpreter (AlphaFraud's venv runs it happily with numpy, pandas,
biotite, plotly). Wheels resolve for gemmi, pyarrow, pandas, duckdb and mlx-lm on 3.14.
`mmseqs2` is not installed but is bottled in Homebrew. `rdkit`, `requests`, `tqdm`, `pyyaml` and
`jsonschema` are already present system-wide.

AlphaFraud's `alphafraud/http.py` (retry and backoff around requests plus a `graphql()` helper) and
`alphafraud/pdb.py` (batched GraphQL against RCSB) are **live, deployed and therefore proven**. Port
them rather than rewriting. This is deliberate: donor code from an unshipped sibling project would
need verifying first, but AlphaFraud has been in production since 2026-07-14.

### 0.6 Cryoprotectant assignment is unreliable in 90% of cases

A 2,683-entry probe across the full release-date range, counting reagents that can act either as a
cryoprotectant or as a genuine component of the drop:

| Reagent | Entries mentioning it | ... that also contain the word "cryo" |
|---------|----------------------|---------------------------------------|
| ethylene glycol | 9.4% | 21 |
| glycerol | 7.5% | 25 |
| PEG 400 | 6.4% | 9 |
| MPD | 4.9% | 4 |
| sucrose | 0.3% | 3 |

**Only 2.2% of entries contain the word "cryo" at all.** So for roughly nine appearances out of ten,
the text offers no evidence whether the reagent was a cryoprotectant or part of the crystallisation
mixture. Worse, glycerol concentrations are not cleanly bimodal: median 12.5%, but **60 of 178
parsed values exceed 15%**, which is precipitant scale rather than cryo scale.

Unmarked, and almost certainly in the drop:

```
1QWY  170 mM ammonium sulfate, 25.5% (w/v) PEG 8K, 15% (v/v) glycerol, pH 6.7
1NRF  18% PEG 8000, 5% Glycerol, 50mM CaCl2 in 0.1M Cacodylate, pH 6.95
```

Explicit, and reliably separable:

```
1GVJ  ...30% W/V PEG 4000... FOR CRYOPROTECTION 10% OF PEG 400 WAS ADDED.
3OTG  ...well solution (20% PEG3350, 0.2M LiSO4, 100mM BisTris pH 6.5) Cryoprotected with 20% ethylene glycol
```

**Consequence for the schema.** A separate `cryo_components[]` block would force a binary decision at
parse time on evidence that is absent in 90% of cases, and misfiling would be recoverable only by
reparsing. Cryo therefore stays a **label** inside `components[]`, carrying its own confidence, and
the fingerprint exclusion in WP5 fires **only on explicit textual evidence**. An inferred cryo
assignment stays in the fingerprint, because on this evidence it was probably in the drop.

### 0.7 The record key is (pdb_id, crystal_id)

`_exptl_crystal_grow` is a **looped** mmCIF category. Entries with several crystal forms carry
several rows, and naive code takes row 0 and silently discards the rest. Every probe in section 0
made exactly that mistake before it was caught.

A 3,000-entry probe suggested 0.07%. **The full ingest gives the exact figure: 390 entries, 0.152%
of the archive**, contributing 495 extra condition rows. The probe was low by a factor of two,
which is the sampling caveat in 0.2 behaving exactly as advertised. The distribution has a long
tail: 342 entries with 2 forms, 27 with 3, 6 with 4, 10 with 5, 3 with 6, 1 with 7 and **one entry
with 21**. Indexing row 0 would have destroyed 20 records from that entry alone.

The rows are often genuinely distinct conditions:

```
1Q8I  row 1  pH 5.3  Citrate, PEG3K, DTT, EDTA, Glycerol
      row 2  pH 5.5  Citrate, PEG3K, DTT, EDTA, Glycerol
      row 3  pH 5.8  Citrate, PEG3K, DTT, MgCl2, Glycerol
3BJQ  row 1  pH 5.79  25.1% PEG 200, 5.0% PEG 3000, 0.1M MES
      row 2  pH 6.00  30.0% PEG 200, 5.0% PEG 3000, 0.1M MES
```

The record key is therefore `(pdb_id, crystal_id)`, with `crystal_id` defaulting to `1`. The cost is
one extra column; the benefit is that an entire class of silent data loss is impossible. The WP1
fidelity gate compares **row counts** as well as string content, since a single-row assumption is
exactly the kind of bug an API-only view hides.

**But not every extra row is a new condition.** 6SVA carries seven forms whose details strings are
identical (`0.2 M Sodium Sulphate 20% PEG 3350` repeated). WP3 must therefore deduplicate identical
rows **within** an entry before counting conditions, or a handful of entries will be over-weighted
in the statistics. Retain the rows, collapse them at analysis time, and record how many collapsed.

---

## 1. Phase 0 definition of done

A release is done when all of the following exist:

1. `crystalball-conditions-v0.1.0` in JSONL, Parquet and DuckDB, one record per
   `(pdb_id, crystal_id)`, conforming to a frozen, versioned JSON Schema.
2. A component-level flat CSV (one row per reagent) for spreadsheet users.
3. Every X-ray entry accounted for: either a record, or a row in the discard table with a coded
   reason. Discard statistics published as a result in their own right.
4. Sequence linkage: construct sequence as crystallised, UniProt accessions, and MMseqs2 cluster
   identifiers at 30%, 50% and 90% identity.
5. A hand-audited stratified sample of 1,500 records with per-field accuracy numbers.
6. An automatic validation number: agreement rate between parsed records and exactly matched
   commercial screen wells.
7. A datasheet documenting provenance, method, schema, biases (spec section 11 verbatim) and
   licence.
8. Per-stage manifests: input hashes, tool versions, git commit, schema version, run timestamp.
9. Public: GitHub repository, Zenodo DOI, HuggingFace dataset mirror, README to house standard.

Explicitly **not** in Phase 0: `curated_group` is emitted as `null` with
`ontology_version: "0.0.0-unassigned"`. The field stays in the schema so Phase 1 is a join, not a
migration.

---

## 2. Work packages

Effort is in working days for one person. Curation hours are called out separately in section 4
because they are the real constraint.

### WP0. Repository, environment, conventions
**2 days. No dependencies.**

- Repo skeleton per spec section 13, rooted at `CrystalBall/`, package at `src/crystal_ball/`.
- `git init`, MIT licence for code, `.gitignore` covering `data/`, `.venv/`, session transcripts.
  Stage files explicitly, never `git add -A`.
- venv on python3.14, `requirements.txt` pinned with a comment per dependency explaining why it is
  there (AlphaFraud house style).
- `brew install mmseqs2`.
- `data/raw` and `data/interim` as symlinks to `~/CrystalBallData/` so nothing large sits inside
  iCloud-synced `Documents`.
- `src/crystal_ball/manifest.py`: one function that every stage calls on exit, writing
  `manifests/<stage>_<timestamp>.json` with input file SHA256s, output SHA256s, package versions,
  git commit, schema version, wall time.
- `run.sh <stage>` dispatcher so each stage is genuinely one command.
- Port `http.py` from AlphaFraud (requests plus tenacity retry plus `graphql()` helper).

**Packages:** `requests`, `tenacity`, `orjson`, `tqdm`, `pyyaml`, `pydantic`, `jsonschema`,
`duckdb`, `pyarrow`, `polars`.

### WP1. Ingest
**3 days. Depends on WP0.**

- `src/crystal_ball/ingest/entry_ids.py`: Search API, all X-ray entry ids plus all experimental
  entry ids for the sequence sidecar (see decision 12.6). Snapshot defines the archive version for
  the whole release. **Built and run 2026-07-28:** the Search API accepts `return_all_hits`, which
  returns all 205,949 identifiers in one response, so pagination is avoided entirely. A paginated
  sweep of a live index can duplicate or drop rows between pages, and the loss would be invisible.
  Sorted by `rcsb_id` for determinism. Counts confirmed at 205,949 X-ray and 256,789 experimental.
- `src/crystal_ball/ingest/fetch_entries.py`: batched GraphQL, 300 ids per request, resumable, raw
  responses written to `data/raw/graphql/<index>_<batch_hash>.json.gz` and never modified again.
  **Measured 2026-07-28:** 1.3 s and 0.58 MB per batch, about 95 KB gzipped, so 857 batches is
  roughly 19 minutes and 80 MB on disk. Requests are serial by choice: the 19 minutes is a one-off
  and parallelising against a free public API to save a quarter of an hour is not a good trade.
- `src/crystal_ball/ingest/flatten.py`: raw JSON to `data/interim/entries.parquet`, **one row per
  `(pdb_id, crystal_id)`** per 0.7, with columns `pdb_id, crystal_id, method, revision_date,
  initial_release_date, resolution, grow_method, temp_k, ph_reported, ph_range, raw_details,
  n_crystal_forms, n_polymer_entities`. The GraphQL query requests `crystal_id` explicitly and the
  flattener iterates the full `exptl_crystal_grow` list, never index 0.
- `src/crystal_ball/ingest/validate_fidelity.py`: **the gate from 0.3.** 200 random entries fetched
  as mmCIF, parsed with gemmi, byte-compared on the details string **and on the row count of the
  `exptl_crystal_grow` loop**. Fails loudly. Oversample deliberately from the roughly 140 known
  multi-row entries so the rare case is actually exercised rather than missed by chance.
- Sequence sidecar: `data/interim/entities.parquet` with
  `pdb_id, entity_id, seq_one_letter, seq_can, length, type, uniprot_ids, source_organism,
  description` for **all** experimental entries, not just X-ray.
- `src/crystal_ball/ingest/targettrack.py`: acquire and checksum the **TargetTrack archive now** into
  `data/raw/targettrack/`. It is archived and unmaintained, and link rot would end the Phase 3
  propensity work with no recovery. **Located 2026-07-28:** Zenodo DOI `10.5281/zenodo.821654`,
  "Protein Structure Initiative - TargetTrack 2000-2017 - all data files",
  `TargetTrack-1Jul2017.tar.gz`, 832.9 MB, final release 1 July 2017. The download URL is resolved
  through the Zenodo API at run time rather than hardcoded, and the published MD5 is verified.
  Stored unopened: which status counts as a positive is decision 12.5 and belongs to Phase 3.

**Packages:** `requests`, `tenacity`, `orjson`, `pyarrow`, `polars`, `gemmi` (validation only).

**Output:** `entries.parquet`, `entities.parquet`, raw JSON archive, manifest.

**WP1 completed 2026-07-28.** Actual results:

| Measure | Value |
|---------|-------|
| Harvest | 256,789 ids requested, 256,789 returned, **0 missing**, 17m57s, 143 MB gzipped |
| Condition rows | 257,284 across 256,789 entries |
| X-ray rows | 206,437 (205,949 distinct entries) |
| X-ray entries with a details string | **198,432** |
| X-ray entries with no grow row at all | 7,433 |
| Multi-form entries | 390 (0.152%), max 21 forms in one entry |
| Polymer entities | 597,595, of which 506,129 (84.7%) carry a UniProt accession |
| Details string length, X-ray | median 76, p90 150, p99 385, max 1,755 characters |

Six independent counts (X-ray entries, with-details, with-pH, with-temp, with-method, all
experimental) were cross-checked against the Search API and **all six match exactly**.

**Fidelity gate: PASSED.** 200 entries, 229 crystal-form rows, zero mismatches on both details
strings and loop row counts. 25 multi-form entries were forced into the sample, where uniform
sampling would have contained 0.3 of them. The API reproduces the archive verbatim on this evidence,
so the build proceeds on the API and the full-scale check happens against the WP9 snapshot.

### WP2. Reagent lexicon (`ontology/synonyms.yaml`)
**3 days, of which 2 are curation. Depends on WP1 (needs the corpus to mine).**

The load-bearing curation artefact of Phase 0. Everything downstream keys off it.

- Mine the 198k details strings for frequency-ranked n-grams and candidate reagent tokens. Rank by
  token mass, not by type count, so effort goes where the corpus is.
- Curate top candidates into canonical entries until 99% of token mass is covered. Expect **250 to
  400 canonical reagents**.
- Per entry: `canonical_id`, `display_name`, `chem_class` (peg, organic, salt, buffer, polyol,
  detergent, other), `aliases[]` (including misspellings seen in the corpus), `default_unit`,
  `default_vv_or_wv`, `peg_mw` where applicable, `hofmeister_rank` for salts, `buffer_pka` and
  `useful_ph_range` for buffers, `pubchem_cid` where trivially available.
- Validated on load by a pydantic model. Alias collisions across canonical entries are a hard error.
- Seed from the JCSG Top96 formulation (Deller 2013) so the taxonomy the ontology will later use is
  already reflected in the naming.

**Packages:** `pyyaml`, `pydantic`, `polars`, `regex`.

**WP2 machinery built 2026-07-28; curation now with Marc.**

`parse.mine_reagents` exploits the structure the text already has: strip the leading
quantity and unit from a clause and what remains is the reagent name. That yields real
chemical phrases where n-gram mining would yield "m sodium" and "glycol 6".

| Measure | Value |
|---------|-------|
| Records mined | 198,918 X-ray, 883,145 clauses |
| Clause types | reagent 64.8%, method 16.0%, pH 9.1%, temperature 7.9%, protein/setup 2.1%, reference-only 0.05% |
| Distinct reagent candidates | 53,876, of which 40,212 seen exactly once |
| Candidates for 50% / 75% / 90% of clause mass | **31 / 256 / 6,174** |
| Seed lexicon | 121 reagents, 452 names, **75.1% clause coverage** |

The tail is long but shallow: 75% of clause mass sits in 256 candidates, and the remaining
25% is mostly singletons. Coverage is therefore reported by **clause mass, not distinct
name**: chasing distinct-name coverage would be a year of work for no measurable gain.

**A spec assumption to revise.** Section 5.1 lists bare `PEG` (no molecular weight) as a
significant discard category. It is not: bare PEG is **771 clauses, 0.14%** of the corpus.
The apparent bulk of it was a parser artefact. When a clause states no concentration, as in
`peg 8000`, a trailing-quantity rule strips the molecular weight as though it were an
amount, leaving a bare `peg` and destroying the reagent's identity. Requiring an explicit
unit before stripping a trailing quantity recovered roughly **15,200 clauses** and moved PEG
from 16.9% to 19.7% of resolved clause mass. Caught by a unit test, not by inspection.

**Awaiting Marc's curation:** `data/interim/lexicon_worklist.csv` holds the top 300 unmapped
candidates with blank `canonical_id` / `chem_class` / `notes` columns to fill in. Four
ambiguous bare anions are deliberately left unmapped rather than guessed: `citrate` (1,348
clauses), `acetate` (948), `phosphate` (283) and bare `peg` (771). Each genuinely varies by
counter-ion, and a wrong guess would be invisible downstream.

### WP3. Deterministic parser
**6 days. Depends on WP2.**

Three named versions, each frozen and manifest-recorded: `rules_v1` (clause splitting and quantity
extraction), `rules_v2` (defaulting rules and pH logic), `rules_v3` (ranges, premixes, edge cases).

- **Clause splitting**: newline, semicolon, comma, ` and `, ` in `, with quantity-aware protection so
  `PEG 6,000` and `0.1 M sodium citrate, pH 5.0` do not split wrongly.
- **Quantity and unit extraction**: `%`, `M`, `mM`, `mg/ml`, bare numbers with implied molarity,
  ranges (`18-22%`, `1.7 to 2.1`), unicode fractions and comma decimal separators (cheap to add,
  rare in practice per 0.2).
- **v/v versus w/v defaulting**, the highest-leverage rule per 0.2: explicit marker wins; otherwise
  default by `chem_class` and, for PEGs, by molecular weight (PEG 600 and below behave as organics
  and are reported v/v; PEG 1000 and above w/v). Record `unit_inferred: true` whenever the default
  fires, so downstream work can filter on it and so the audit can measure the rule directly.
- **PEG molecular weight normalisation**: `3350`, `3,350`, `3.35K`, `PEG MME 2000`,
  `PEG monomethyl ether 550`, `PEGMME550`, `polyethylene glycol 8K` all to
  `(canonical_id=PEG_3350, peg_mw=3350, is_mme=bool)`.
- **pH logic**: capture value and range; classify `ph_source` as `buffer`, `final` or `unstated`
  using proximity to a buffer token versus a trailing standalone `pH x`. Reconcile against the
  structured `exptl_crystal_grow.pH` field, which is present for only 150k of 206k entries and
  disagrees with the text often enough to be worth reporting as a finding.
- **Temperature**: K versus C disambiguation (anything between 0 and 45 with no unit is Celsius;
  above 250 is Kelvin), reconciled with the structured `temp` field.
- **Roles**: precipitant, salt, buffer, additive, cryo, protein, unknown. Per 0.6, `role: cryo`
  carries its own `role_confidence` and a `cryo_evidence` field taking `explicit` (the text names it
  as a cryoprotectant, roughly 2% of entries) or `inferred` (assigned from reagent identity and
  concentration alone). The two are never conflated, because only the first is trustworthy.
- **Opportunistic capture** of protein concentration, drop ratio and equilibration volume where
  present. Spec section 11.4 correctly says they are usually missing; capture them anyway, as a
  nullable block, because nobody else has.
- **Confidence**: per-clause score from rule specificity, aggregated to `parse_confidence`.
  Uncovered residual text is the dominant penalty term.
- **Discard taxonomy** with stable codes: `EMPTY`, `TOO_SHORT`, `NO_REAGENT_MATCH`,
  `REFERENCE_ONLY`, `METHOD_ONLY`, `UNPARSEABLE_RESIDUAL`, `NON_CRYSTALLISATION_TEXT`. Every
  discarded entry gets exactly one code. The discard table ships with the release.
- **Frozen test fixture**: 300 hand-picked cases including all of spec 5.2, as a pytest suite.
  Written before `rules_v2`, not after.

**Packages:** `regex`, `pydantic`, `jsonschema`, `polars`, `pytest`.

**WP3 built 2026-07-29. Rule parser at 90.9% keep rate, well above the expected plateau.**

Spec 5.2 predicts a rule-based parser plateaus around 75%. On this corpus `rules_v3` keeps
**181,103 of 199,185 records (90.9%)** with a median confidence of 1.000, and resolves
**77.0% of components** to a canonical reagent against a 121-entry lexicon. The gap between
those two numbers is where the WP6 language model earns its place: most records parse, but
roughly a quarter of components still land as `unknown`.

| Discard reason | Records | Share |
|----------------|---------|-------|
| NO_REAGENT_MATCH | 10,537 | 5.3% |
| TOO_SHORT | 4,369 | 2.2% |
| METHOD_ONLY | 2,258 | 1.1% |
| UNPARSEABLE_RESIDUAL | 613 | 0.3% |
| REFERENCE_ONLY | 107 | 0.1% |
| EMPTY | 91 | 0.0% |
| NON_CRYSTALLISATION_TEXT | 107 | 0.1% |

`REFERENCE_ONLY` is 107 records. Spec 5.1 treats "see publication" as a major discard
category; on the evidence it is negligible, consistent with the planning probe finding 0 of
600. The real discard driver is unresolved reagent names, which curation directly improves.

**Four separator bugs found by smoke-testing on real strings, each silently destructive:**

1. **Newlines were being flattened before clause splitting.** Depositors routinely put one
   component per line, so every multi-line deposition was parsed as a single unresolvable
   clause. The parser normalised the text and then split it, when it had to split first.
2. **`in` was not a separator**, so `50mM CaCl2 in 0.1M cacodylate` resolved neither
   reagent. Now split only when followed by a digit, leaving `grown in sitting drops` alone.
3. **Multiple spaces were not a separator**, so `glycol 6,000  100 mM citrate` merged two
   components. Now split on two or more spaces followed by a digit.
4. **Leading conjunctions blocked lookup**: `and 172 mM ammonium nitrate` never resolved.

**Measured behaviour of the three judgement calls:**

- **Unit inference** fired on 31.5% of components (187,390). Every one is flagged
  `unit_inferred`, and WP8 measures it separately.
- **pH attribution**: 88,845 buffer, 50 final, 92,208 unstated. The tiny `final` count is
  the honest answer, not a failure: depositors almost never say which they mean, and the
  parser refuses to guess. A standalone pH clause directly following a buffer clause is
  attributed to that buffer, which is the commonest way the corpus writes it.
- **Cryo attribution**: 3,724 explicit against 16,795 inferred, a 1:4 ratio that matches the
  2.2% textual-mention rate from 0.6 and confirms why the two must not be conflated.

### WP4. Commercial screen formulation library
**3 days, of which 2 are curation. Depends on WP2 (shares the canonical vocabulary). Parallel with WP3.**

- Digitise Hampton Crystal Screen 1 and 2, Index, PEGRx, SaltRx, PEG/Ion; Molecular Dimensions
  JCSG-plus, PACT, Morpheus; Qiagen JCSG Core I to IV; Rigaku Wizard I to IV; and the JCSG Top96.
  Roughly 1,500 to 2,000 wells.
- One YAML per screen in `ontology/screens/`, each well expressed as a component list **in the same
  canonical schema as a parsed record**, so matching is schema to schema rather than text to text.
- Cross-check each screen against a second independent source (vendor sheet plus Newman's C6, or
  vendor sheet plus a published table) and record which sources were used per screen. Transcription
  errors here silently corrupt the validation set, so this is not the place to save time.
- Keep `ontology/screens/` structurally separable from the rest of the release, in case a vendor
  objects to redistribution of a transcription (see D7).

**Packages:** `pyyaml`, `pydantic`.

### WP5. Screen matching and the optimised versus screen-hit flag
**2 days. Depends on WP3 and WP4.**

- Match each parsed record against the well library: component set equality on canonical ids, then
  per-`chem_class` concentration tolerance, then pH tolerance. Emit
  `commercial_screen_match {screen, well, exact}` and a match distance.
- **Fingerprint definition, per 0.6.** The component set used for matching excludes cryo components
  **only where `cryo_evidence == explicit`**. Components tagged `cryo` by inference stay in. This
  matters: roughly a third of glycerol values sit above 15%, which is precipitant scale, and
  excluding those would corrupt otherwise correct matches. Report the match rate both ways once, as
  a check that the rule is doing what is intended.
- `exact` requires all concentrations within tight tolerance and the same component set. Near
  matches with non-standard concentrations set `provenance.flags += optimised_not_screen`, per spec
  5.4.
- **The free validation set.** Exact matches are strong evidence of a correct parse. Report the
  match rate and use disagreements (parse says A, nearest well says B with one component differing)
  as a triage queue for the parser. Expect this to catch real bugs faster than hand auditing does.

**Packages:** `polars`, `numpy`.

**WP4/WP5 built 2026-07-29.** 9 Hampton screens, 434 wells, all sourced from the vendor's own
support-material PDFs and extracted verbatim. Nothing transcribed from memory: the screen name
and catalogue number are read out of each document, and screens whose layout defeats extraction
(the HT 96-well binders) are **rejected rather than shipped incomplete**, because a library with
six invented wells would corrupt the validation set far more quietly than a missing screen.

**All 434 wells parse to fully resolved components (100%).** That is a direct check on the
parser: vendor strings are clean prose, and a parser that stumbles on
`0.2 M Magnesium chloride hexahydrate, 0.1 M TRIS hydrochloride pH 8.5, 30% w/v Polyethylene
glycol 4,000` has no business on the messy corpus. Getting there from 81.3% surfaced four
generic normalisation gaps that also help the main corpus: hydration state
(`calcium chloride dihydrate`), oxidation state (`nickel(ii) chloride`), trademark symbols
(`tacsimatetm`, `jeffamine ®`), and the spaced-slash titration separator
(`0.98 M sodium malonate pH 7.0 / 0.02 M citric acid`).

| Matching result | Records | Share |
|-----------------|---------|-------|
| Kept records | 181,103 | |
| Component set matches a known well | 42,296 | 23.4% |
| ... and every concentration agrees | 18,584 | 43.9% of matches |
| ... and the pH agrees too (exact) | 10,024 | |
| Flagged `optimised_not_screen` | 23,712 | |

**Reading the 43.9% honestly.** It is not a parser accuracy figure. Deviation analysis over
85,258 matched components shows the median deposited/screen concentration ratio is exactly
**1.00**, with **64% within 5%** of the published value and 88% within 2x. The mode is exact
agreement; the spread above it is dominated by genuine optimisation, which is precisely what
spec 5.4 predicts. Two caveats belong in any write-up:

- **Component-set matching is necessary but not sufficient.** A single-component condition such
  as `2.0 M ammonium sulfate` matches Crystal Screen 32 trivially, whether or not the depositor
  ever used that screen. Simple conditions inflate the match count.
- **A systematic naming error is invisible here**, because both sides use the same parser and the
  same lexicon. Screen matching validates structure, concentration and unit reading. Reagent
  naming is what the WP8 hand audit is for.

**One bug this analysis caught.** `concentrations_agree` compared unit *strings*, so a well
printed as `0.1 M` and a deposition written as `100 mM` scored as a disagreement. That alone
accounted for 11.6% of matched components. Reducing molar-family units to a common scale before
comparison raised concentration agreement from 38.3% to 43.9% and exact matches from 8,773 to
10,024. Percent w/v and percent v/v are deliberately kept in separate families.

### WP6. Small language model parser for the residual
**6 days, of which 3 are labelling. Depends on WP3, WP5.**

- **Target only the residual.** Records where `rules_v3` reports low confidence, leaves uncovered
  text, or disagrees with the nearest exact screen match. Expect 20% to 25% of the corpus.
- **Labels.** Bootstrap from high-confidence `rules_v3` output, then hand-correct 3,000 to 5,000
  records prioritised by low confidence and by rules-versus-screen disagreement. Correct a
  **random 500 as well as the prioritised set**: training only on hard cases teaches the model that
  hard cases are typical and biases it.
- **The circularity trap, stated explicitly.** Labels bootstrapped from the rule parser cannot be
  used to prove the model beats the rule parser. The 1,500-record audit set of WP8 is hand-labelled
  from raw text, held out, and **never used for training or threshold tuning**. Every headline parse
  accuracy number in the paper comes from that set alone.
- **Model.** SmolLM2-360M under MLX-LM LoRA, prompt masked, constrained to schema-valid JSON,
  W&B logging. Start at 135M; go to 360M only if the smaller model fails schema validity.
- **Two iterations** of parse the archive, sample the rules-versus-model disagreements, correct,
  retrain, per spec 5.3.
- **Reconciliation policy, fixed in advance**: rules win where confident; the model fills the
  residual; disagreements above threshold go to the audit queue rather than being resolved
  silently. `provenance.parser` records which produced each record.
- Run `preflight.sh` before every training launch. Do not disable `grad_checkpoint`.

**Packages:** `mlx`, `mlx-lm`, `wandb`, `huggingface_hub`, `jsonschema`.

### WP7. Sequence linkage and redundancy control
**4 days. Depends on WP1 only. Fully parallel with WP2 to WP6.**

- Construct sequences from `entity_poly.pdbx_seq_one_letter_code` (as crystallised, tags included),
  with the canonical form kept alongside.
- UniProt accessions from `rcsb_polymer_entity_container_identifiers.uniprot_ids`.
- **SIFTS** `pdb_chain_uniprot.tsv.gz` (segment level) is sufficient for Phase 0. Residue-level
  SIFTS XML is a Phase 3 requirement for the boundary work and is not needed here. Download and
  checksum it now regardless.
- UniProt full-length reference sequences for all mapped accessions, fetched in bulk via the UniProt
  REST stream endpoint.
- **Complex policy, per spec 7.2**: the condition record is labelled with the **largest polymer
  entity**, carries `is_complex` and the full entity list, and is **never duplicated per chain**.
- **MMseqs2 `easy-cluster` at 30%, 50% and 90%** identity over all crystallised sequences. All three
  cluster ids go into every record. 90% is included specifically so that Phase 2 decision 12.4
  (multi-label ground truth construction) can be answered later without re-clustering.

**Packages:** `mmseqs2` (shelled out), `requests`, `polars`, `biotite` (sequence handling).

### WP8. Audit, metrics and quality assurance
**4 days, of which 1.5 are curation. Depends on WP5, WP6, WP7.**

- **Stratified sample of 1,500** records across `chem_class` of the leading precipitant, release era,
  confidence band, and producing parser. Sampling code is deterministic and seeded.
- **A single-file HTML audit tool** in `app/audit.html`: vanilla JavaScript, Tabulator for the table,
  marcdeller.com brand theme, loads a JSON slice, shows raw text beside parsed components, records
  corrections, exports JSON. No server. This is inside the project's build constraints, and a decent
  interface is the difference between 1.5 days of auditing and a week of it.
- **Metrics**: per-field accuracy (pH, temperature, method), component-level precision, recall and F1
  against the hand audit, unit-inference accuracy measured separately (per 0.2 it carries most of
  the risk), discard-reason table, and the screen-match agreement rate from WP5.
- **Disagreement analysis** between the reported `exptl_crystal_grow.pH` field and the pH parsed from
  text. A publishable finding in its own right and free.

**Packages:** `polars`, `numpy`, `scikit-learn` (metrics only), `plotly`.

### WP9. Release
**3 days. Depends on WP8.**

- **Archive snapshot and full-scale fidelity check**, per the decision in 0.3. Rsync the 90 GB
  divided mmCIF archive to a location outside `Documents`, re-parse `_exptl_crystal_grow` for every
  entry with gemmi, and compare against the ingested records on both string content and row count.
  Publish the agreement rate and any disagreement list as part of the datasheet. Record the archive
  date and total byte count in the manifest. The snapshot may be deleted afterwards; the agreement
  report is the artefact that matters. Budget half a day plus download time, and confirm the disk
  has 90 GB free before starting.
- Freeze the JSON Schema, version it independently of the data (`schema v1.0.0`,
  `crystalball-conditions v0.1.0`).
- Emit JSONL (canonical), Parquet, DuckDB, and the component-level flat CSV.
- **Datasheet**: provenance, archive snapshot date, method, schema, per-field accuracy, discard
  statistics, and spec section 11's biases and limitations reproduced in full. The biases section is
  not an appendix, it goes near the top.
- Publish: GitHub (code, MIT), Zenodo (data, DOI, CC-BY-4.0), HuggingFace datasets under `Dellboy`.
  Use `env -u HF_TOKEN` on every `hf` command.
- README written through the house skill, not hand-written markdown, with the model and dataset
  tables in the established append-only format.
- A static statistics page is a stretch goal only. The browser front end belongs to Phase 2.

**Packages:** `duckdb`, `pyarrow`, `huggingface_hub`.

---

## 3. Dependency order and schedule

```
WP0 ─▶ WP1 ─┬─▶ WP2 ─┬─▶ WP3 ─────┬─▶ WP5 ─▶ WP6 ─┬─▶ WP8 ─▶ WP9
            │        └─▶ WP4 ─────┘               │
            └─▶ WP7 ───────────────────────────────┘
```

| Week | Work |
|------|------|
| 1 | WP0, WP1 (including the fidelity gate and the TargetTrack grab) |
| 2 | WP2 lexicon curation, WP7 started in parallel |
| 3 | WP3 parser, WP4 screen library curation interleaved |
| 4 | WP3 finished, WP5 matching, WP7 finished |
| 5 | WP6 labelling and two fine-tune iterations |
| 6 | WP8 audit and metrics, WP9 release |

Total 33 working days, which lands inside the spec's 4 to 6 week estimate only if the curation in
section 4 is treated as scheduled work rather than something squeezed in around it.

**Critical path:** WP0, WP1, WP2, WP3, WP5, WP6, WP8, WP9. WP4 and WP7 have slack. The two schedule
risks are the WP2 lexicon (everything downstream is blocked on it) and WP6 labelling (3 days of
concentrated human attention that cannot be compressed by tooling alone).

---

## 4. Where the hand-curation time actually goes

| Activity | Hours | When | Notes |
|----------|-------|------|-------|
| Reagent lexicon, 250 to 400 canonical entries | 12 to 16 | WP2 | Frequency-ranked, so the first 4 hours cover most of the corpus |
| Commercial screen transcription, 1,500 to 2,000 wells | 12 to 16 | WP4 | Two independent sources per screen; the slow part is cross-checking |
| SLM label correction, 3,000 to 5,000 records | 15 to 20 | WP6 | Two rounds. At roughly 12 seconds per record with the audit tool |
| Stratified audit, 1,500 records | 8 to 10 | WP8 | Held out from training, never reused |
| Edge case triage and discard adjudication | 4 to 6 | WP3, WP5 | Driven by the screen-match disagreement queue |
| **Total** | **51 to 68** | | Roughly 7 to 9 working days of human attention |

The spec's "a weekend of work, not a year" is right in spirit and light by a factor of three. Budget
a fortnight of curation spread across the six weeks. Building the WP8 audit tool early, and using it
for WP6 labelling as well as WP8 auditing, is what keeps this number down.

---

## 5. Section 12 open decisions: what blocks Phase 0

| # | Decision | Blocks Phase 0? | Recommendation |
|---|----------|-----------------|----------------|
| 12.1 | Number of L2 and L3 groups, orphan threshold | **No** | Phase 1. Emit `curated_group: null` and `ontology_version: "0.0.0-unassigned"`. Keeping the field in the schema makes Phase 1 a join, not a migration |
| 12.2 | Morpheus and PACT: own class or `mixed_system` | **Partly** | The *taxonomy* is Phase 1 and can wait. The *representation* cannot: WP3 must decide how premixed stocks are stored. Recommend expanding each premix into its published constituent components while carrying a `premix_id` (for example `MORPHEUS_CARBOXYLIC_ACIDS_MIX`), so no information is lost and either Phase 1 choice remains available |
| 12.3 | Cryoprotectant in the group, or parallel field | **Yes, and DECIDED 2026-07-28** | Cryo components stay in `components[]` with `role: cryo`, a `role_confidence`, a `cryo_evidence` field (`explicit` or `inferred`) and a derived `cryo_present` boolean. Fingerprint exclusion fires **only on explicit evidence**. Driven by the measurement in 0.6: only 2.2% of entries say "cryo" at all, so a separate block would force a binary call on absent evidence and lose real precipitants |
| 12.4 | Multi-label ground truth: entry, 90% or 50% cluster | **No** | Phase 2. The only Phase 0 obligation is to ship 30%, 50% **and 90%** cluster ids so all three options stay open. Covered in WP7 |
| 12.5 | Which TargetTrack transition counts as positive | **No** | Phase 3. But **acquire and checksum the dump in WP1**. It is archived and unmaintained, and losing it would end the propensity work. One hour now |
| 12.6 | Include cryo-EM and NMR entries | **Yes, as ingest scope** | Agree with the spec: exclude from conditions, but ingest polymer entity sequences and UniProt links for **all** experimental entries (256,789 rather than 205,949). Adds about an hour to WP1 and avoids a full re-ingest when Phase 3 boundary work starts |
| 12.7 | Licensing and hosting | **Yes, by week 5** | Recommend CC-BY-4.0 for data, MIT for code, Zenodo for the DOI, HuggingFace `Dellboy` as the working mirror, GitHub for code. Caveat: `ontology/screens/` is a transcription of vendor formulations. Formulations are published facts and citing the vendor is normal practice, but keep that directory separable so it can be pulled without breaking the release if anyone objects |

**A new decision, surfaced by the WP1 ingest and not anticipated in the spec.** 194 non-X-ray entries
carry a populated `exptl_crystal_grow` record: neutron diffraction 88, electron crystallography 69,
powder diffraction 24, electron microscopy 10, fibre diffraction 9, solution scattering 1. The first
three are real crystals grown by real crystallographers, and their conditions are as valid as any
X-ray entry's. They are 0.09% of the corpus, so nothing statistical hangs on it, but the scope needs
stating rather than defaulting. **Recommendation:** admit neutron, electron crystallography and
powder diffraction to the condition corpus with a `diffraction_method` field so they can be filtered
out at will, and exclude the EM, fibre and solution-scattering handful as probably mis-annotated.
Not blocking: it changes 181 records and can be revisited at any point before release.

Three decisions needed answering before code lands: **12.3**, **12.6**, and the representation half
of **12.2**. **12.3 is now decided** (see the table). **12.6** and the **12.2** representation
question are proceeding as recommended above, since both are lossless and reversible: say so if you
want either revisited. **12.7** needs answering by week 5, not week 1, and now has a hard
dependency, since the WP9 archive snapshot needs 90 GB free at the same time.

---

## 6. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| API text differs from archive mmCIF | Low | High | WP1 gate on 200 entries, then the full-scale check against the archive snapshot in WP9 |
| Multi-row `exptl_crystal_grow` silently truncated | **Was certain** | Medium | Caught during planning. Record key is `(pdb_id, crystal_id)`, flattener never indexes row 0, gate compares row counts and oversamples the ~140 multi-row entries |
| Inferred cryo labels treated as fact | Medium | Medium | `cryo_evidence` separates `explicit` from `inferred`; only explicit ones affect the fingerprint |
| Disk lacks 90 GB when WP9 runs | Medium | Medium | Check free space in week 4, not week 6. Snapshot is deletable once the agreement report is written |
| Unit inference (v/v versus w/v) wrong at scale | **Medium** | **High** | 80% of percentages carry no marker. Measured separately in WP8, flagged per record with `unit_inferred` |
| Screen transcription errors poison the validation set | Medium | High | Two independent sources per screen, recorded per screen in the manifest |
| SLM evaluated on rules-derived labels, circularly | Medium | High | Audit set hand-labelled from raw text, held out, never used for training or thresholds |
| Curation underestimated, schedule slips | **High** | Medium | Section 4 budgets 51 to 68 hours explicitly; audit tool built early and reused for labelling |
| RCSB rate limits or partial batch failures | Medium | Low | Resumable, on-disk batch cache keyed by hash, tenacity backoff |
| iCloud evicts large intermediate files | Medium | Medium | `data/raw` and `data/interim` symlinked outside `Documents`; dataless check before every long run |
| Python 3.14 wheel gaps in a later dependency | ~~Low~~ **Closed** | Low | All core dependencies including gemmi 0.7.5 install cleanly on 3.14.3 (verified WP0) |
| Editable install silently not importable | **Was certain** | Medium | Every pip-installed file in site-packages on this machine carries the macOS `UF_HIDDEN` flag, and python 3.14's `site.py` skips hidden `.pth` files, so setuptools' `__editable__*.pth` is ignored while `pip list` still shows the package. Fixed by putting `src` on the path explicitly in `run.sh` and `pytest`'s `pythonpath`, which does not regress on reinstall |

---

## 7. Immediate next actions

Ingest route and 12.3 are decided, so the path is clear:

1. **WP0**: repository, venv on python3.14, `brew install mmseqs2`, manifest module, `http.py` ported
   from AlphaFraud, `data/` symlinked outside `Documents`.
2. **WP1**: entry id snapshot, batched GraphQL ingest keyed on `(pdb_id, crystal_id)`, fidelity gate
   including row counts, TargetTrack dump acquired and checksummed.
3. **WP2**: mine the corpus for reagent n-grams and start the lexicon, which is the item everything
   else is blocked on.

Nothing downstream should start before the WP1 fidelity gate passes.

Open, but not blocking today: **12.7** licensing and hosting, needed by week 5.
