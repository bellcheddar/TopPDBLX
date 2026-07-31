# ⚖️ Condition Courtroom

Single-file audit interfaces for the TopPDBLX parse. No server, no build step, no npm: open the
file in a browser and drop a JSON payload on it. Work saves to `localStorage`, so closing the tab
costs nothing.

## 📦 Versions

Five versions ship, because the interaction model changed twice and the earlier ones are kept as
the record of why. **Use v5.**

| Version | Payload | Decisions | Model |
|---|---|---|---|
| `condition_courtroom_v1.html` | `audit_sample.json` | 1,484 | One verdict per record |
| `condition_courtroom_v2.html` | `audit_sample.json` | 1,484 | As v1, plus a fix for an intermittent Tabulator load failure |
| `condition_courtroom_v3.html` | `audit_decisions.json` | 919 | Distinct decisions ranked by corpus frequency, default-accept |
| `condition_courtroom_v4.html` | `audit_questions.json` | 35 | Only the calls a human must make, with pre-computed dropdowns |
| **`condition_courtroom_v5.html`** | **any questions payload** | **tens** | **v4 generalised: payload-driven title, multi-line questions, per-payload export filename** |

The progression is the point. Auditing 1,484 records one at a time ignores that the corpus is
enormously redundant: judging "20% peg 3350 becomes PEG_3350" for the thirty-thousandth time buys
no new evidence. v3 fixed that by auditing distinct decisions weighted by how many components each
governs. v4 went further and asked which decisions need a human at all: only those where the
pipeline **guessed**, **contradicted itself**, or **refused to choose**. That is 35 questions, not
1,484 records. v5 changed nothing about the interaction and everything about reuse: it renders any
payload with the same shape, so a new audit is a new generator stage rather than a new page.

### Rounds run so far

| Round | Generator | Questions | Reach |
|---|---|---|---|
| Parse audit | `eval.audit_questions` | 35 | Unit inference and reagent naming across the corpus |
| Condition groups | `assign.group_questions` | 26 | The 163-group ontology |
| Lexicon gaps | `parse.lexicon_questions` | 40 | 7,018 unresolved components |

## ⚠️ A recommendation is an answer, not a suggestion

Every dropdown is pre-set and there is an accept-all button, so a wrong recommendation does not
get caught: it gets accepted in bulk. Generators must therefore be conservative, and
`parse.lexicon_questions` carries explicit chemistry guards after its first version proposed
`peg 3400` to PEG 400 (an eightfold molecular weight error) and `1,3-propanediol` to the 1,2
isomer.

Guards decide what is **recommended**, never what is **available**: a rejected candidate stays in
the list labelled `NOT recommended: numbers differ ...`, because hiding it would put the correct
answer out of reach. Where a name is genuinely ambiguous (bare `peg`, bare `phosphate`) the
recommendation is to leave it unresolved rather than invent a member the depositor never named.

## 🚀 Usage

```bash
./run.sh parse.lexicon_questions     # writes data/interim/lexicon_questions.json
open app/condition_courtroom_v5.html # drop the JSON on the page
```

Answer, export, then apply. Each round has its own apply stage, so the ontology stays
reproducible from corpus plus answers rather than hand-patched:

```bash
./run.sh parse.apply_lexicon_answers --dry-run   # review before writing
./run.sh parse.apply_lexicon_answers
./run.sh eval.audit_metrics --verdicts ~/Downloads/audit_answers.json
```

**Only touched questions are exported.** A question absent from the export is an accepted
recommendation, not an unanswered one, and the apply stages record which of the two each change
was so the distinction survives into the changelog.

To re-run after applying answers, and see only outstanding work:

```bash
./run.sh eval.audit_questions \
  --previous-answers data/interim/audit_answers.json \
  --reask unit::salt::millimolar
```

## ⌨️ Keyboard

v4 and v5 are questionnaires and need no shortcuts. v3 is keyboard-driven, because at several
hundred cards the interaction cost is the whole budget.

| Key | Action (v3) |
|---|---|
| <kbd>J</kbd> / <kbd>K</kbd>, arrows | Move the cursor |
| <kbd>F</kbd> or <kbd>Space</kbd> | Flag the current card as wrong |
| <kbd>A</kbd> | Accept everything above the cursor |
| <kbd>1</kbd> to <kbd>4</kbd> | Switch section |

## 🛠️ Notes

- **No CDN dependency from v3 onwards.** v1 and v2 load Tabulator from unpkg and degrade to a
  message if it is unreachable; v3, v4 and v5 work fully offline.
- **`localStorage` keys are namespaced per tool version and payload**, so answers from one round do
  not silently attach to a different question set.
- **Exports overwrite the same filename.** Archive each round: completed rounds live in
  `data/interim/audit_rounds/`.
