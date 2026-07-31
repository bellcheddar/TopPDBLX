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
| Screens | 24 (22 reagent-named, 2 compositional) |
| Wells | 1,374 |
| Components identified in the 22 reagent-named screens | 96.8% (2,772 of 2,863) |
| Component-set matches against the corpus | 60,225 |
| Matches agreeing on every concentration | 28,257 |

The 91 unidentified components are all in the three NeXtal suites, whose PDF text layer runs the
salt, buffer and precipitant columns together and splits numbers at kerning pairs. They are
extraction damage rather than unknown chemistry: every other screen reads at 100%.

**Two screens are stored compositionally.** Morpheus and PACT premier name vendor stocks (Buffer
System, Precipitant Mix, Divalents, SPG) rather than reagents, so they are held verbatim and
excluded from the identification figure above. Expanding them would mean asserting constituents
the plate table does not state.

**A match is not a validation of reagent naming.** Both the screen library and the parsed
conditions are identified through the same lexicon, so a systematic naming error moves both sides
identically and the agreement rate would not notice. The matches test concentration and
composition, not nomenclature.

Screen names and product codes are trademarks of their respective owners. Their use here is
nominative, to identify which formulation is meant, and implies no endorsement or affiliation.

See `../../LICENSE-DATA` for the full position.
