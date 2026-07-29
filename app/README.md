# ⚖️ Condition Courtroom

Single-file audit interfaces for the TopPDBLX parse. No server, no build step, no npm: open the
file in a browser and drop a JSON payload on it. Work saves to `localStorage`, so closing the tab
costs nothing.

## 📦 Versions

Four versions ship, because the interaction model changed twice and the earlier ones are kept as
the record of why. **Use v4.**

| Version | Payload | Decisions | Model |
|---|---|---|---|
| `condition_courtroom_v1.html` | `audit_sample.json` | 1,484 | One verdict per record |
| `condition_courtroom_v2.html` | `audit_sample.json` | 1,484 | As v1, plus a fix for an intermittent Tabulator load failure |
| `condition_courtroom_v3.html` | `audit_decisions.json` | 919 | Distinct decisions ranked by corpus frequency, default-accept |
| **`condition_courtroom_v4.html`** | **`audit_questions.json`** | **35** | **Only the calls a human must make, with pre-computed dropdowns** |

The progression is the point. Auditing 1,484 records one at a time ignores that the corpus is
enormously redundant: judging "20% peg 3350 becomes PEG_3350" for the thirty-thousandth time buys
no new evidence. v3 fixed that by auditing distinct decisions weighted by how many components each
governs. v4 went further and asked which decisions need a human at all: only those where the
pipeline **guessed**, **contradicted itself**, or **refused to choose**. That is 35 questions, not
1,484 records.

## 🚀 Usage

```bash
./run.sh eval.audit_questions        # writes data/interim/audit_questions.json
open app/condition_courtroom_v4.html # drop the JSON on the page
```

Answer, export, then apply:

```bash
./run.sh eval.audit_metrics --verdicts ~/Downloads/audit_answers.json
```

To re-run after applying answers, and see only outstanding work:

```bash
./run.sh eval.audit_questions \
  --previous-answers data/interim/audit_answers.json \
  --reask unit::salt::millimolar
```

## ⌨️ Keyboard

v4 is a questionnaire and needs no shortcuts. v3 is keyboard-driven, because at several hundred
cards the interaction cost is the whole budget.

| Key | Action (v3) |
|---|---|
| <kbd>J</kbd> / <kbd>K</kbd>, arrows | Move the cursor |
| <kbd>F</kbd> or <kbd>Space</kbd> | Flag the current card as wrong |
| <kbd>A</kbd> | Accept everything above the cursor |
| <kbd>1</kbd> to <kbd>4</kbd> | Switch section |

## 🛠️ Notes

- **No CDN dependency in v3 and v4.** v1 and v2 load Tabulator from unpkg and degrade to a message
  if it is unreachable; v3 and v4 work fully offline.
- **`localStorage` keys are namespaced per tool version and payload**, so answers from one round do
  not silently attach to a different question set.
- **Exports overwrite the same filename.** Archive each round: completed rounds live in
  `data/interim/audit_rounds/`.
