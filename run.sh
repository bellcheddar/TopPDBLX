#!/usr/bin/env bash
# Crystal Ball stage dispatcher.
#
# Build constraint from the spec: "Everything reproducible from a single command per
# stage." This is that command. Every stage is a python module under crystal_ball and
# writes its own manifest.
#
#   ./run.sh ingest.entry_ids            # snapshot the X-ray + experimental entry id lists
#   ./run.sh ingest.fetch_entries        # batched GraphQL harvest
#   ./run.sh ingest.flatten              # raw JSON -> entries.parquet, entities.parquet
#   ./run.sh ingest.validate_fidelity    # the WP1 gate: API vs mmCIF, content and row count
#   ./run.sh --list                      # show available stages

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$REPO_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "error: $PYTHON not found. Create it with:" >&2
  echo "  python3.14 -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'" >&2
  exit 1
fi

if [[ $# -lt 1 || "${1:-}" == "--list" || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "usage: ./run.sh <stage> [args...]"
  echo
  echo "available stages:"
  find "$REPO_ROOT/src/crystal_ball" -name '*.py' \
    -not -name '__init__.py' -not -name 'config.py' \
    -not -name 'http.py' -not -name 'manifest.py' \
    | sed "s|$REPO_ROOT/src/crystal_ball/||; s|\.py$||; s|/|.|g" \
    | sort | sed 's/^/  /'
  exit 0
fi

STAGE="$1"; shift
exec "$PYTHON" -m "crystal_ball.$STAGE" "$@"
