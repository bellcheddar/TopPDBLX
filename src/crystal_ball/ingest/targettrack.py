"""Stage `ingest.targettrack`: acquire and checksum the TargetTrack archive.

TargetTrack is the merged TargetDB/PepcDB record of structural genomics target status, and
it is the **only** substantial source of failed crystallisation targets. Without it there is
no crystallisation propensity model, only a model conditioned on success (spec section 11.1).

It is not needed until Phase 3. It is fetched now anyway, in Phase 0, for one reason: the
resource is archived and unmaintained, final release 1 July 2017, and losing it would end
the propensity work with no recovery. An hour of bandwidth today removes that risk.

Source: Zenodo DOI 10.5281/zenodo.821654, "Protein Structure Initiative - TargetTrack
2000-2017 - all data files". The download URL is resolved through the Zenodo API at run
time rather than hardcoded, and the published MD5 is verified, so a truncated or
substituted file fails loudly instead of sitting on disk pretending to be data.

The archive is stored as-is and deliberately not unpacked or parsed here. Which target
status counts as a positive is spec decision 12.5, and it belongs to Phase 3.

    ./run.sh ingest.targettrack
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any, Optional

from .. import config, http
from ..manifest import Manifest

STAGE = "ingest.targettrack"

ZENODO_RECORD = 821654
ZENODO_API = "https://zenodo.org/api/records/{record}"
EXPECTED_FILENAME = "TargetTrack-1Jul2017.tar.gz"


def resolve_file(record: int = ZENODO_RECORD) -> dict[str, Any]:
    """Return the Zenodo file entry for the TargetTrack tarball."""
    body = http.get_json(ZENODO_API.format(record=record))
    if not body:
        raise RuntimeError(f"Zenodo record {record} returned no content")
    files = body.get("files") or []
    for entry in files:
        if entry.get("key") == EXPECTED_FILENAME:
            return {
                "filename": entry["key"],
                "size": entry.get("size"),
                "checksum": entry.get("checksum"),
                "url": entry["links"]["self"],
                "doi": body.get("doi"),
                "title": body.get("metadata", {}).get("title"),
            }
    raise RuntimeError(
        f"{EXPECTED_FILENAME} not found in Zenodo record {record}; "
        f"available: {[f.get('key') for f in files]}"
    )


def md5sum(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=config.RAW_TARGETTRACK_DIR)
    parser.add_argument("--record", type=int, default=ZENODO_RECORD)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    with Manifest(STAGE, params={"zenodo_record": args.record}) as m:
        meta = resolve_file(args.record)
        dest = args.out_dir / meta["filename"]
        print(f"{meta['title']}\n  doi {meta['doi']}\n  {meta['size'] / 1e6:.1f} MB -> {dest}")

        http.download(meta["url"], dest, skip_if_exists=True)
        if not dest.exists():
            raise RuntimeError(f"download produced no file at {dest}")

        actual_size = dest.stat().st_size
        if meta["size"] and actual_size != meta["size"]:
            raise RuntimeError(f"size mismatch: expected {meta['size']}, got {actual_size}")

        published = meta.get("checksum") or ""
        verified = None
        if published.startswith("md5:"):
            actual = md5sum(dest)
            verified = actual == published.split(":", 1)[1]
            if not verified:
                raise RuntimeError(f"MD5 mismatch: published {published}, computed md5:{actual}")
            print(f"  MD5 verified against the published checksum")
        else:
            print(f"  no published MD5 to verify against (checksum field: {published!r})")

        m.add_output(dest).note(
            zenodo_doi=meta["doi"],
            zenodo_title=meta["title"],
            source_url=meta["url"],
            published_checksum=published,
            checksum_verified=verified,
            bytes=actual_size,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
