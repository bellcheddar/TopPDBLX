"""Paths, endpoints and the version constants every stage stamps into its manifest.

Nothing here reads the network or touches disk on import: importing config must stay free
so that `--help` on any stage is instant.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# data/{raw,interim,processed} are symlinks to ~/CrystalBallData/. They deliberately live
# outside Documents: macOS Optimize Mac Storage evicts large files from iCloud-synced
# folders, which turns a 90 GB archive snapshot into dataless stubs without warning.
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
ONTOLOGY_DIR = REPO_ROOT / "ontology"
MANIFEST_DIR = REPO_ROOT / "manifests"

RAW_GRAPHQL_DIR = RAW_DIR / "graphql"
RAW_MMCIF_DIR = RAW_DIR / "mmcif"
RAW_SIFTS_DIR = RAW_DIR / "sifts"
RAW_UNIPROT_DIR = RAW_DIR / "uniprot"
RAW_TARGETTRACK_DIR = RAW_DIR / "targettrack"

# ---------------------------------------------------------------------------
# External endpoints
# ---------------------------------------------------------------------------
RCSB_SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"
RCSB_GRAPHQL_URL = "https://data.rcsb.org/graphql"
RCSB_FILE_URL = "https://files.rcsb.org/download/{entry_id}.{fmt}"

# Measured 2026-07-28: 90,004,014,513 bytes across 258,044 files. Used only by the WP9
# release-time snapshot, never during the normal build.
RCSB_RSYNC_HOST = "rsync.rcsb.org::ftp_data/structures/divided/mmCIF"
RCSB_RSYNC_PORT = 33444

SIFTS_CHAIN_UNIPROT_URL = (
    "https://ftp.ebi.ac.uk/pub/databases/msd/sifts/flatfiles/tsv/pdb_chain_uniprot.tsv.gz"
)
UNIPROT_STREAM_URL = "https://rest.uniprot.org/uniprotkb/stream"

# ---------------------------------------------------------------------------
# HTTP behaviour
# ---------------------------------------------------------------------------
HTTP_TIMEOUT = float(os.environ.get("CB_HTTP_TIMEOUT", "60"))
HTTP_MAX_RETRIES = int(os.environ.get("CB_HTTP_MAX_RETRIES", "5"))
USER_AGENT = "CrystalBall/0.1 (+https://github.com/marcdeller; marc@marcdeller.com)"

# 300 verified working against data.rcsb.org on 2026-07-28. Larger batches risk the
# gateway timing out mid-response, which costs more than the extra round trips save.
GRAPHQL_BATCH_SIZE = int(os.environ.get("CB_GRAPHQL_BATCH", "300"))
SEARCH_PAGE_SIZE = 10_000

# ---------------------------------------------------------------------------
# Versions: every manifest records all three, and every released record carries them
# ---------------------------------------------------------------------------
SCHEMA_VERSION = "0.1.0-draft"       # frozen to 1.0.0 at WP9
DATASET_VERSION = "0.1.0-dev"        # crystalball-conditions
# Phase 0 does no group assignment: curated_group is null throughout. The field exists so
# Phase 1 is a join rather than a schema migration.
ONTOLOGY_VERSION = "0.0.0-unassigned"


def ensure_dirs() -> None:
    """Create the writable output directories. Called by stages, not on import."""
    for d in (
        RAW_GRAPHQL_DIR, RAW_MMCIF_DIR, RAW_SIFTS_DIR, RAW_UNIPROT_DIR,
        RAW_TARGETTRACK_DIR, INTERIM_DIR, PROCESSED_DIR, MANIFEST_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
