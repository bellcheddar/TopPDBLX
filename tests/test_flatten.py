"""Tests for the GraphQL-to-Parquet flattener.

The multi-crystal-form cases are modelled on 1Q8I (three forms at pH 5.3, 5.5 and 5.8),
which is a real entry and the reason the record key is (pdb_id, crystal_id) rather than
pdb_id. Silently keeping only the first form is the single most plausible data-loss bug in
this pipeline, so it is tested directly.
"""

from __future__ import annotations

from toppdblx.ingest.flatten import _as_float, entity_rows, entry_rows


def make_entry(**overrides):
    entry = {
        "rcsb_id": "1ABC",
        "exptl": [{"method": "X-RAY DIFFRACTION"}],
        "exptl_crystal_grow": [
            {"crystal_id": "1", "method": "VAPOR DIFFUSION, HANGING DROP", "temp": 293.0,
             "temp_details": None, "pH": 6.5, "pdbx_pH_range": None,
             "pdbx_details": "20% PEG 3350, 0.2 M NaCl", "details": None},
        ],
        "rcsb_accession_info": {
            "initial_release_date": "2008-07-08T00:00:00Z",
            "revision_date": "2024-10-30T00:00:00Z",
            "deposit_date": "2007-10-02T00:00:00Z",
            "major_revision": 1, "minor_revision": 4,
        },
        "rcsb_entry_info": {
            "resolution_combined": [2.5], "polymer_entity_count": 1,
            "experimental_method": "X-ray",
        },
        "polymer_entities": [],
    }
    entry.update(overrides)
    return entry


# --- crystal form handling ------------------------------------------------

def test_single_crystal_form():
    rows = list(entry_rows(make_entry()))
    assert len(rows) == 1
    row = rows[0]
    assert (row["pdb_id"], row["crystal_id"]) == ("1ABC", "1")
    assert row["n_crystal_forms"] == 1
    assert row["is_xray"] is True
    assert row["raw_details"] == "20% PEG 3350, 0.2 M NaCl"
    assert row["temp_k"] == 293.0
    assert row["ph_reported"] == 6.5
    assert row["resolution_a"] == 2.5


def test_multiple_crystal_forms_all_survive():
    """1Q8I-shaped: three forms, three records, no silent truncation."""
    entry = make_entry(exptl_crystal_grow=[
        {"crystal_id": "1", "pH": 5.3, "pdbx_details": "Citrate, PEG3K, DTT, EDTA, Glycerol"},
        {"crystal_id": "2", "pH": 5.5, "pdbx_details": "Citrate, PEG3K, DTT, EDTA, Glycerol"},
        {"crystal_id": "3", "pH": 5.8, "pdbx_details": "Citrate, PEG3K, DTT, MgCl2, Glycerol"},
    ])
    rows = list(entry_rows(entry))
    assert len(rows) == 3
    assert [r["crystal_id"] for r in rows] == ["1", "2", "3"]
    assert [r["ph_reported"] for r in rows] == [5.3, 5.5, 5.8]
    # Every row records how many forms the entry had, so the rare case is filterable.
    assert {r["n_crystal_forms"] for r in rows} == {3}
    # The third condition genuinely differs; it must not be collapsed into the others.
    assert "MgCl2" in rows[2]["raw_details"]
    assert len({(r["pdb_id"], r["crystal_id"]) for r in rows}) == 3


def test_missing_crystal_id_falls_back_to_position():
    entry = make_entry(exptl_crystal_grow=[
        {"pdbx_details": "first"},
        {"crystal_id": None, "pdbx_details": "second"},
    ])
    rows = list(entry_rows(entry))
    assert [r["crystal_id"] for r in rows] == ["1", "2"]


def test_entry_with_no_grow_rows_still_produces_a_record():
    """Needed so WP3 can account for every X-ray entry with a discard reason."""
    for empty in (None, []):
        rows = list(entry_rows(make_entry(exptl_crystal_grow=empty)))
        assert len(rows) == 1
        assert rows[0]["n_crystal_forms"] == 0
        assert rows[0]["crystal_id"] == "1"
        assert rows[0]["raw_details"] is None
        assert rows[0]["is_xray"] is True


def test_non_xray_entry_is_flagged():
    entry = make_entry(exptl=[{"method": "ELECTRON MICROSCOPY"}], exptl_crystal_grow=None)
    row = next(iter(entry_rows(entry)))
    assert row["is_xray"] is False
    assert row["exptl_methods"] == ["ELECTRON MICROSCOPY"]


def test_missing_resolution_is_none_not_error():
    entry = make_entry(rcsb_entry_info={"resolution_combined": None,
                                        "polymer_entity_count": 1,
                                        "experimental_method": "NMR"})
    assert next(iter(entry_rows(entry)))["resolution_a"] is None


# --- entities -------------------------------------------------------------

def test_entity_rows_keep_both_sequences_and_uniprot():
    entry = make_entry(polymer_entities=[{
        "rcsb_id": "1ABC_1",
        "entity_poly": {
            "type": "polypeptide(L)",
            "pdbx_seq_one_letter_code": "ACD(MSE)FG",
            "pdbx_seq_one_letter_code_can": "ACDMFG",
            "rcsb_sample_sequence_length": 6,
            "pdbx_strand_id": "A,B",
        },
        "rcsb_polymer_entity": {"pdbx_description": "Test protein", "formula_weight": 12.3},
        "rcsb_polymer_entity_container_identifiers": {
            "uniprot_ids": ["P12345"], "asym_ids": ["A"], "auth_asym_ids": ["A", "B"],
        },
        "rcsb_entity_source_organism": [
            {"ncbi_scientific_name": "Homo sapiens", "ncbi_taxonomy_id": 9606},
        ],
    }])
    row = next(iter(entity_rows(entry)))
    assert row["entity_id"] == "1ABC_1"
    # The raw form carries the modified residue; the canonical form is what MMseqs2 sees.
    assert row["seq"] == "ACD(MSE)FG"
    assert row["seq_can"] == "ACDMFG"
    assert row["uniprot_ids"] == ["P12345"]
    assert row["source_organisms"] == ["Homo sapiens"]
    assert row["source_taxids"] == [9606]
    assert row["is_xray"] is True


def test_entity_rows_tolerate_absent_blocks():
    entry = make_entry(polymer_entities=[{
        "rcsb_id": "1ABC_1",
        "entity_poly": {"type": "polyribonucleotide"},
        "rcsb_polymer_entity": None,
        "rcsb_polymer_entity_container_identifiers": None,
        "rcsb_entity_source_organism": None,
    }])
    row = next(iter(entity_rows(entry)))
    assert row["uniprot_ids"] == []
    assert row["source_organisms"] == []
    assert row["description"] is None


# --- numeric coercion -----------------------------------------------------

def test_as_float_coerces_and_tolerates_junk():
    assert _as_float(293) == 293.0
    assert _as_float("6.5") == 6.5
    for junk in (None, "", "room temperature", "n/a"):
        assert _as_float(junk) is None
