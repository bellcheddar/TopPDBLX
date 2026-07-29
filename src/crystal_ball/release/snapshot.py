"""Stage `release.snapshot`: take the frozen mmCIF archive snapshot for the release.

The decision in plan section 0.3 was to build on the RCSB Data API and take **one** archive
snapshot at release time, rather than mirroring 90 GB on day one. This is that snapshot.

Its whole purpose is to turn a reproducibility hand-wave into a measured claim. With it,
`release.verify_archive` can compare the parsed source field against the archive for **every**
entry rather than the 200 sampled at WP1, and the dataset paper can state an agreement rate as
of a dated archive version.

Measured 2026-07-28: 90,004,014,513 bytes across 258,044 files. The snapshot is deletable once
the agreement report is written; the report is the artefact that matters, not the bytes.

    ./run.sh release.snapshot
    ./run.sh release.snapshot --dry-run      # size and file count only
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .. import config
from ..manifest import Manifest

STAGE = "release.snapshot"

# The archive must not live inside Documents: macOS Optimize Mac Storage evicts large files
# from iCloud-synced folders, which would turn the snapshot into dataless stubs.
DEFAULT_DEST = Path.home() / "CrystalBallData" / "raw" / "mmcif_archive"

HEADROOM_BYTES = 20 * 1024**3      # keep 20 GB spare after the transfer


def free_bytes(path: Path) -> int:
    usage = shutil.disk_usage(path if path.exists() else path.parent)
    return usage.free


def rsync_command(dest: Path, dry_run: bool) -> list[str]:
    command = ["rsync", "-rlpt", "--stats", "--port", str(config.RCSB_RSYNC_PORT)]
    if dry_run:
        command.append("-n")
    command += [config.RCSB_RSYNC_HOST + "/", str(dest) + "/"]
    return command


def parse_stats(output: str) -> dict[str, int]:
    stats = {}
    for key, pattern in (("n_files", r"Number of files:\s*([\d,]+)"),
                         ("total_bytes", r"Total file size:\s*([\d,]+)"),
                         ("transferred_bytes", r"Total transferred file size:\s*([\d,]+)")):
        match = re.search(pattern, output)
        if match:
            stats[key] = int(match.group(1).replace(",", ""))
    return stats


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--dry-run", action="store_true",
                        help="report size and file count without transferring")
    parser.add_argument("--force", action="store_true",
                        help="proceed even if free space looks insufficient")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.dest.mkdir(parents=True, exist_ok=True)

    with Manifest(STAGE, params={"dest": str(args.dest), "dry_run": args.dry_run,
                                 "source": config.RCSB_RSYNC_HOST}) as m:
        if not args.dry_run:
            probe = subprocess.run(rsync_command(args.dest, dry_run=True),
                                   capture_output=True, text=True)
            needed = parse_stats(probe.stdout).get("total_bytes", 0)
            available = free_bytes(args.dest)
            print(f"archive is {needed / 1e9:.1f} GB; {available / 1e9:.1f} GB free")
            if needed and available < needed + HEADROOM_BYTES and not args.force:
                raise RuntimeError(
                    f"insufficient space: need {needed / 1e9:.1f} GB plus "
                    f"{HEADROOM_BYTES / 1e9:.0f} GB headroom, have {available / 1e9:.1f} GB. "
                    f"Use --force to override."
                )

        result = subprocess.run(rsync_command(args.dest, args.dry_run),
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"rsync failed ({result.returncode}):\n{result.stderr[-2000:]}")

        stats = parse_stats(result.stdout)
        on_disk = sum(1 for _ in args.dest.rglob("*.cif.gz")) if not args.dry_run else 0
        m.add_output(args.dest).note(**stats, n_files_on_disk=on_disk,
                                     dry_run=args.dry_run)

        print(f"\n{'would transfer' if args.dry_run else 'snapshot complete'}: "
              f"{stats.get('n_files', 0):,} files, "
              f"{stats.get('total_bytes', 0) / 1e9:.1f} GB")
        if not args.dry_run:
            print(f"  {on_disk:,} .cif.gz files on disk at {args.dest}")
            print("  next: ./run.sh release.verify_archive")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
