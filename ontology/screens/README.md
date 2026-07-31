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
| Screens | 60 (58 reagent-named, 2 compositional) |
| Wells | 2,526 |
| Components identified in the 58 reagent-named screens | 93.4% (5,547 of 5,942) |
| Component-set matches against the corpus | 76,951 |
| Matches agreeing on every concentration | 35,993 |
| Vendors | Hampton Research 21, Jena Bioscience 30, Rigaku/MiTeGen 4, Qiagen NeXtal 3, Molecular Dimensions 2 |

The unidentified components are now mostly real chemistry the lexicon has not met rather than
extraction damage, which is the healthier of the two failures. Two families dominate: Rigaku
writes buffers as conjugate pairs ("HEPES/ Sodium hydroxide", "Imidazole/ Hydrochloric acid"),
and Jena's Pentaerythritol series uses the vendor abbreviations PEP 629 and PEE 270, which the
brochure's own product listing confirms are pentaerythritol propoxylate and ethoxylate. Both are
curation queues. The remainder is the three NeXtal suites, whose PDF text layer runs the salt,
buffer and precipitant columns together and splits numbers at kerning pairs.

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
