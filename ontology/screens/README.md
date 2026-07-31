# Commercial screen formulations

Each file here is a transcription of one crystallisation screen's published formulation,
extracted verbatim from the vendor's own publicly available support materials by
`./run.sh assign.build_screens`. Nothing is transcribed from memory, and the screen name and
catalogue number are read out of the source document rather than assumed.

**Separable by design.** This directory can be deleted without breaking the release. The
condition database, the sequence linkage and the parser are all unaffected; only the
`commercial_screen_match` field and the free parse-validation number depend on it. That
separation is deliberate, so a vendor objection can be accommodated by removing one directory
rather than by withdrawing the dataset.

## 📊 What is here

| Measure | Value |
|---|---|
| Screens | 9 |
| Wells | 434, all of which identify to typed components |
| Component-set matches against the corpus | 45,547 |
| Matches agreeing on every concentration | 20,339 |

Every L3 group in the condition ontology is anchored to one of these wells, so a group can name
something orderable rather than a set of numbers.

**A match is not a validation of reagent naming.** Both the screen library and the parsed
conditions are identified through the same lexicon, so a systematic naming error moves both sides
identically and the agreement rate would not notice. The matches test concentration and
composition, not nomenclature.

Screen names and product codes are trademarks of their respective owners. Their use here is
nominative, to identify which formulation is meant, and implies no endorsement or affiliation.

See `../../LICENSE-DATA` for the full position.
