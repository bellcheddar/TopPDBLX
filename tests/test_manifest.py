"""Tests for the per-stage manifest writer.

Every stage in the pipeline depends on this module, and its most important behaviour is
the one that is easiest to get wrong: writing a manifest even when the stage fails.
"""

from __future__ import annotations

import json

import pytest

from toppdblx import config
from toppdblx.manifest import Manifest


@pytest.fixture(autouse=True)
def _isolated_manifest_dir(tmp_path, monkeypatch):
    """Never write test manifests into the repo's manifests/ directory."""
    monkeypatch.setattr(config, "MANIFEST_DIR", tmp_path / "manifests")


def _only_manifest(tmp_path) -> dict:
    files = list((tmp_path / "manifests").glob("*.json"))
    assert len(files) == 1, f"expected exactly one manifest, got {files}"
    return json.loads(files[0].read_text())


def test_writes_manifest_on_success(tmp_path):
    src = tmp_path / "input.txt"
    src.write_text("hello")
    out = tmp_path / "output.txt"
    out.write_text("world")

    with Manifest("test.success", params={"batch_size": 300}) as m:
        m.add_input(src)
        m.add_output(out)
        m.note(n_entries=205949)

    data = _only_manifest(tmp_path)
    assert data["stage"] == "test.success"
    assert data["status"] == "ok"
    assert data["error"] is None
    assert data["params"] == {"batch_size": 300}
    assert data["notes"] == {"n_entries": 205949}
    assert data["versions"]["ontology"] == "0.0.0-unassigned"
    assert data["inputs"][0]["sha256"] == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"  # sha256("hello")
    )
    assert data["outputs"][0]["bytes"] == 5
    assert data["environment"]["packages"]["gemmi"] is not None


def test_writes_manifest_on_failure_and_reraises(tmp_path):
    with pytest.raises(ValueError, match="boom"):
        with Manifest("test.failure") as m:
            m.note(got_this_far=True)
            raise ValueError("boom")

    data = _only_manifest(tmp_path)
    assert data["status"] == "failed"
    assert data["error"]["type"] == "ValueError"
    assert data["error"]["message"] == "boom"
    assert "ValueError: boom" in data["error"]["traceback"]
    # Notes recorded before the failure survive: they are how a failed run is diagnosed.
    assert data["notes"] == {"got_this_far": True}


def test_missing_path_is_recorded_not_raised(tmp_path):
    with Manifest("test.missing") as m:
        m.add_input(tmp_path / "does_not_exist.parquet")

    data = _only_manifest(tmp_path)
    assert data["status"] == "ok"
    assert data["inputs"][0]["exists"] is False


def test_directory_is_summarised_not_enumerated(tmp_path):
    raw = tmp_path / "graphql"
    raw.mkdir()
    for i in range(3):
        (raw / f"batch_{i}.json.gz").write_bytes(b"x" * (i + 1))

    with Manifest("test.directory") as m:
        m.add_input(raw)

    entry = _only_manifest(tmp_path)["inputs"][0]
    assert entry["kind"] == "directory"
    assert entry["n_files"] == 3
    assert entry["total_bytes"] == 6
    assert len(entry["listing_sha256"]) == 64


def test_large_file_hash_is_skipped_with_a_reason(tmp_path, monkeypatch):
    """A 90 GB archive snapshot must not be hashed byte by byte."""
    monkeypatch.setattr("toppdblx.manifest.HASH_SIZE_LIMIT", 4)
    big = tmp_path / "archive.tar"
    big.write_bytes(b"12345")

    with Manifest("test.large") as m:
        m.add_output(big)

    entry = _only_manifest(tmp_path)["outputs"][0]
    assert entry["sha256"] is None
    assert "larger than" in entry["hash_skipped_reason"]
    assert entry["bytes"] == 5
