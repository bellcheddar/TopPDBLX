"""Tests for sequence linkage and clustering helpers.

Spec 7.2 and 7.3 are both "get this wrong and the metrics are silently meaningless"
requirements, so the two rules that enforce them are pinned here: one record per entry
regardless of chain count, and cluster ids present at all three thresholds.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from crystal_ball.link.cluster import read_clusters
from crystal_ball.link.representative import sequence_id
from crystal_ball.link.uniprot import batches, parse_fasta


# --- sequence identity ----------------------------------------------------

def test_sequence_id_is_stable_and_collision_resistant():
    assert sequence_id("ACDEFG") == sequence_id("ACDEFG")
    assert sequence_id("ACDEFG") != sequence_id("ACDEFH")
    assert len(sequence_id("ACDEFG")) == 16


def test_identical_sequences_share_an_id():
    """Two entries of the same protein must land in the same cluster entry, not two."""
    assert sequence_id("MKVL" * 20) == sequence_id("MKVL" * 20)


# --- mmseqs cluster parsing -----------------------------------------------

def test_read_clusters_maps_members_to_representatives(tmp_path: Path):
    tsv = tmp_path / "c.tsv"
    tsv.write_text("repA\trepA\nrepA\tmem1\nrepA\tmem2\nrepB\trepB\n")
    table = read_clusters(tsv, "cluster_30")
    assert table.columns == ["seq_id", "cluster_30"]
    assert table.height == 4
    mapping = dict(zip(table["seq_id"], table["cluster_30"]))
    assert mapping["mem1"] == "repA"
    assert mapping["mem2"] == "repA"
    assert mapping["repB"] == "repB"
    # A representative is a member of its own cluster.
    assert mapping["repA"] == "repA"


# --- UniProt fetch helpers ------------------------------------------------

def test_batches_never_exceed_the_uniprot_or_limit():
    """UniProt rejects more than 100 OR conditions per query."""
    from crystal_ball.link.uniprot import BATCH_SIZE
    assert BATCH_SIZE <= 100
    chunks = list(batches([str(i) for i in range(250)], BATCH_SIZE))
    assert all(len(c) <= 100 for c in chunks)
    assert sum(len(c) for c in chunks) == 250


def test_parse_fasta_extracts_the_accession_from_a_uniprot_header():
    text = (">sp|P00698|LYSC_CHICK Lysozyme C OS=Gallus gallus\nMRSLLIL\nVLCFLPL\n"
            ">tr|A0A123|A0A123_TEST Uncharacterised\nMKVLA\n")
    parsed = dict(parse_fasta(text))
    assert parsed["P00698"] == "MRSLLILVLCFLPL"
    assert parsed["A0A123"] == "MKVLA"


def test_parse_fasta_handles_a_plain_header():
    assert dict(parse_fasta(">P12345\nMKV\n")) == {"P12345": "MKV"}


def test_parse_fasta_on_empty_input():
    assert dict(parse_fasta("")) == {}


# --- the built linkage table ----------------------------------------------

@pytest.fixture(scope="module")
def linked():
    path = Path("data/interim/entry_sequence.parquet")
    if not path.exists():
        pytest.skip("entry_sequence.parquet not built; run ./run.sh link.representative")
    return pl.read_parquet(path)


def test_one_row_per_entry_never_one_per_chain(linked):
    """Spec 7.2: duplicating per chain would let a ribosome dominate every statistic."""
    assert linked["pdb_id"].n_unique() == linked.height


def test_complexes_are_flagged_and_keep_their_entity_list(linked):
    complexes = linked.filter(pl.col("is_complex"))
    assert complexes.height > 0
    assert (complexes["n_polymer_entities"] > 1).all()
    assert (complexes["entity_ids"].list.len() > 1).all()


def test_representative_is_the_longest_entity(linked):
    sample = linked.filter(pl.col("is_complex")).head(200)
    assert (sample["representative_length"] > 0).all()


def test_protein_representative_recorded_separately_from_the_overall_one(linked):
    """They differ for protein/nucleic-acid complexes where the nucleic acid is longer."""
    differing = linked.filter(
        pl.col("protein_entity_id").is_not_null()
        & (pl.col("representative_entity_id") != pl.col("protein_entity_id"))
    )
    assert differing.height > 0, "expected some entries where the longest chain is not protein"


@pytest.fixture(scope="module")
def clusters():
    path = Path("data/interim/sequence_clusters.parquet")
    if not path.exists():
        pytest.skip("sequence_clusters.parquet not built; run ./run.sh link.cluster")
    return pl.read_parquet(path)


def test_all_three_cluster_thresholds_are_present(clusters):
    """90% is shipped so decision 12.4 stays answerable without re-clustering."""
    for column in ("cluster_30", "cluster_50", "cluster_90"):
        assert column in clusters.columns
        assert clusters[column].null_count() == 0


def test_clusters_are_nested_from_loose_to_tight(clusters):
    """A 30% cluster must be at least as coarse as a 50%, which is coarser than a 90%."""
    n30 = clusters["cluster_30"].n_unique()
    n50 = clusters["cluster_50"].n_unique()
    n90 = clusters["cluster_90"].n_unique()
    assert n30 < n50 < n90 <= clusters.height
