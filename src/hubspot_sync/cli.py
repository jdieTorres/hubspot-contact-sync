"""Command-line entry point.

    python -m hubspot_sync sync sample_data/contacts.csv
    python -m hubspot_sync sync sample_data/contacts.csv --dry-run
    python -m hubspot_sync export contacts_export.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

from .client import HubSpotClient
from .sync import run_sync

EXPORT_PROPERTIES = ["email", "firstname", "lastname", "phone", "company", "city", "hs_language"]


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Tiny .env loader so the project has no extra dependency for it."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hubspot_sync", description="Sync contacts between a CSV and HubSpot.")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="Upsert contacts from a CSV into HubSpot")
    p_sync.add_argument("csv", type=Path)
    p_sync.add_argument("--out", type=Path, default=Path("output"), help="Folder for logs, checkpoint and failures")
    p_sync.add_argument("--dry-run", action="store_true", help="Validate and batch without calling the API")

    p_export = sub.add_parser("export", help="Export all contacts from HubSpot to a CSV (paginated)")
    p_export.add_argument("csv", type=Path)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    _load_dotenv()
    token = os.environ.get("HUBSPOT_TOKEN", "")

    if args.command == "sync":
        client = None if args.dry_run else HubSpotClient(token)
        s = run_sync(client, args.csv, args.out, dry_run=args.dry_run)
        print(
            f"\nRows read: {s.rows_read} | rejected: {s.rejected_before_send} | duplicates merged: {s.duplicates_merged}\n"
            f"Batches: {s.batches_total} (skipped via checkpoint: {s.batches_skipped_from_checkpoint})\n"
            f"Created: {s.created} | updated: {s.updated} | failed: {s.failed} | {s.duration_seconds}s\n"
            f"Details: {args.out}/"
        )
        return 0 if s.ok else 2  # non-zero exit lets cron/CI alert on problems

    if args.command == "export":
        client = HubSpotClient(token)
        count = 0
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["id", *EXPORT_PROPERTIES])
            writer.writeheader()
            for c in client.iter_contacts(EXPORT_PROPERTIES):
                props = c.get("properties", {})
                writer.writerow({"id": c["id"], **{p: props.get(p) or "" for p in EXPORT_PROPERTIES}})
                count += 1
        print(f"Exported {count} contacts to {args.csv}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
