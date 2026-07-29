"""Tests for the release stages."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from toppdblx.release.assemble import build_records
from toppdblx.release.snapshot import parse_stats
from toppdblx.release.verify_archive import archive_path


# --- archive layout -------------------------------------------------------

def test_archive_path_uses_the_divided_layout():
    """The PDB divided archive keys on the middle two characters, lowercased."""
    assert archive_path(Path("/a"), "4HHB") == Path("/a/hh/4hhb.cif.gz")
    assert archive_path(Path("/a"), "1abc") == Path("/a/ab/1abc.cif.gz")


# --- rsync stats parsing --------------------------------------------------

def test_parse_stats_reads_thousands_separators():
    output = """
    Number of files: 258,436 (reg: 258,000, dir: 436)
    Total file size: 90,004,014,513 bytes
    Total transferred file size: 1,234 bytes
    """
    stats = parse_stats(output)
    assert stats["n_files"] == 258436
    assert stats["total_bytes"] == 90004014513
    assert stats["transferred_bytes"] == 1234


def test_parse_stats_on_unexpected_output():
    assert parse_stats("something went wrong") == {}


# --- record assembly ------------------------------------------------------

@pytest.fixture
def frames():
    conditions = pl.DataFrame([{
        "pdb_id": "1ABC", "crystal_id": "1", "entry_version": "2024-01-01",
        "raw_details": "20% PEG 3350", "method": "VAPOR DIFFUSION", "diffraction_method": "X-RAY DIFFRACTION",
        "temperature_k": 293.0, "temperature_source": "reported", "ph": 7.5,
        "ph_source": "buffer", "ph_is_range": False, "ph_reported": 7.5,
        "protein_concentration_mg_ml": None, "drop_ratio": None,
        "n_components": 1, "n_components_resolved": 1, "parse_confidence": 1.0,
        "discard_reason": None, "flags": ["unit_inferred"], "parser": "rules_v3",
    }])
    components = pl.DataFrame([{
        "pdb_id": "1ABC", "crystal_id": "1", "component_index": 0, "role": "precipitant",
        "name_raw": "peg 3350", "name_canonical": "PEG_3350", "chem_class": "peg",
        "peg_mw": 3350, "is_mme": False, "hofmeister_rank": None, "buffer_pka": None,
        "concentration": 20.0, "unit": "percent_w_v", "unit_inferred": True,
        "concentration_is_range": False, "range_low": None, "range_high": None,
        "cryo_evidence": None, "premix_id": None,
    }])
    sequences = pl.DataFrame([{
        "pdb_id": "1ABC", "protein_entity_id": "1ABC_1", "protein_seq": "ACDEFG",
        "protein_length": 6, "protein_description": "Test", "protein_uniprot_ids": ["P1"],
        "is_complex": False, "n_polymer_entities": 1, "seq_id": "abc123",
        "cluster_30": "c30", "cluster_50": "c50", "cluster_90": "c90",
    }])
    matches = pl.DataFrame([{
        "pdb_id": "1ABC", "crystal_id": "1", "screen": "Crystal Screen",
        "catalogue": "HR2-110", "well": "32", "match_type": "exact",
    }])
    return conditions, components, sequences, matches


def test_build_records_nests_components_and_sequence(frames):
    records = build_records(*frames)
    assert len(records) == 1
    record = records[0]
    assert record["pdb_id"] == "1ABC"
    assert len(record["components"]) == 1
    assert record["components"][0]["name_canonical"] == "PEG_3350"
    assert record["sequence"]["cluster_30"] == "c30"
    assert record["commercial_screen_match"]["well"] == "32"


def test_curated_group_ships_null_so_phase_1_is_a_join(frames):
    """Present but empty: shipping the field now avoids a schema migration later."""
    record = build_records(*frames)[0]
    assert "curated_group" in record
    assert record["curated_group"] is None


def test_records_carry_all_three_versions(frames):
    record = build_records(*frames)[0]
    for key in ("schema_version", "ontology_version", "dataset_version"):
        assert record[key]


def test_record_is_json_serialisable(frames):
    """The canonical form is JSONL, so anything unserialisable breaks the release."""
    record = build_records(*frames)[0]
    assert json.loads(json.dumps(record))["pdb_id"] == "1ABC"


def test_entry_without_a_sequence_still_produces_a_record(frames):
    conditions, components, _, matches = frames
    empty = pl.DataFrame(schema={
        "pdb_id": pl.Utf8, "protein_entity_id": pl.Utf8, "protein_seq": pl.Utf8,
        "protein_length": pl.UInt32, "protein_description": pl.Utf8,
        "protein_uniprot_ids": pl.List(pl.Utf8), "is_complex": pl.Boolean,
        "n_polymer_entities": pl.UInt32, "seq_id": pl.Utf8,
        "cluster_30": pl.Utf8, "cluster_50": pl.Utf8, "cluster_90": pl.Utf8})
    record = build_records(conditions, components, empty, matches)[0]
    assert record["sequence"] is None
    assert record["pdb_id"] == "1ABC"


def test_discarded_records_keep_their_raw_text_and_reason(frames):
    """A user who disagrees with a discard needs the evidence to argue with."""
    conditions, components, sequences, matches = frames
    conditions = conditions.with_columns(pl.lit("TOO_SHORT").alias("discard_reason"))
    record = build_records(conditions, components, sequences, matches)[0]
    assert record["discard_reason"] == "TOO_SHORT"
    assert record["raw_details"] == "20% PEG 3350"


# --- the built release ----------------------------------------------------

@pytest.fixture(scope="module")
def released():
    path = Path("data/processed")
    files = list(path.glob("toppdblx-conditions-v*.parquet")) if path.exists() else []
    if not files:
        pytest.skip("release not built; run ./run.sh release.assemble")
    return pl.read_parquet(files[0])


def test_release_key_is_unique(released):
    assert released.select(["pdb_id", "crystal_id"]).unique().height == released.height


def test_every_record_either_parsed_or_has_a_reason(released):
    unexplained = released.filter(
        pl.col("discard_reason").is_null() & (pl.col("n_components") == 0))
    assert unexplained.height == 0


def test_release_carries_cluster_ids_for_linked_records(released):
    linked = released.filter(pl.col("seq_id").is_not_null())
    assert linked.height > 0
    assert linked.filter(pl.col("cluster_30").is_null()).height == 0
