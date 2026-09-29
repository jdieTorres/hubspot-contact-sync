"""Sync pipeline: CSV -> clean -> batches of 100 -> upsert, with checkpoint, failed-records file and run summary."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .client import BATCH_LIMIT, HubSpotClient, HubSpotError
from .transform import transform_rows

log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    started_at: str
    finished_at: str = ""
    source: str = ""
    rows_read: int = 0
    rejected_before_send: int = 0
    duplicates_merged: int = 0
    batches_total: int = 0
    batches_skipped_from_checkpoint: int = 0
    created: int = 0
    updated: int = 0
    failed: int = 0
    duration_seconds: float = 0.0
    dry_run: bool = False
    failures: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.rejected_before_send == 0


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def file_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def chunked(items: list, size: int = BATCH_LIMIT):
    for i in range(0, len(items), size):
        yield items[i : i + size]


class Checkpoint:
    """Remembers the last batch that finished, so a crashed run resumes instead of starting over."""

    def __init__(self, path: Path, fingerprint: str):
        self.path = path
        self.fingerprint = fingerprint
        self.last_done = -1
        if path.exists():
            data = json.loads(path.read_text())
            if data.get("fingerprint") == fingerprint:
                self.last_done = data.get("last_batch_done", -1)

    def mark(self, batch_index: int) -> None:
        self.last_done = batch_index
        self.path.write_text(json.dumps({"fingerprint": self.fingerprint, "last_batch_done": batch_index}))

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


def run_sync(
    client: HubSpotClient | None,
    source: Path,
    out_dir: Path,
    dry_run: bool = False,
    batch_size: int = BATCH_LIMIT,
) -> RunSummary:
    start = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = RunSummary(started_at=_now(), source=str(source), dry_run=dry_run)

    rows = read_csv(source)
    summary.rows_read = len(rows)
    result = transform_rows(rows)
    summary.rejected_before_send = len(result.rejected)
    summary.duplicates_merged = result.duplicates_merged
    summary.failures.extend({"stage": "validation", **r} for r in result.rejected)

    batches = list(chunked(result.valid, batch_size))
    summary.batches_total = len(batches)
    checkpoint = Checkpoint(out_dir / "checkpoint.json", file_fingerprint(source))

    for i, batch in enumerate(batches):
        if i <= checkpoint.last_done:
            summary.batches_skipped_from_checkpoint += 1
            continue
        if dry_run:
            log.info("[dry-run] batch %d/%d: %d contacts", i + 1, len(batches), len(batch))
            continue

        try:
            resp = client.batch_upsert_contacts(batch)
        except HubSpotError as exc:
            # Whole batch rejected (e.g. 400 invalid property) or retries exhausted: record and keep going.
            log.error("batch %d failed: %s", i + 1, exc)
            summary.failed += len(batch)
            summary.failures.extend(
                {"stage": "api", "batch": i + 1, "email": r["email"], "error": str(exc)} for r in batch
            )
        else:
            for rec in resp.get("results", []):
                if rec.get("new"):
                    summary.created += 1
                else:
                    summary.updated += 1
            for err in resp.get("errors", []):  # HTTP 207 multi-status: partial failure
                ids = err.get("context", {}).get("ids") or err.get("context", {}).get("id") or ["?"]
                for rid in ids if isinstance(ids, list) else [ids]:
                    summary.failed += 1
                    summary.failures.append(
                        {"stage": "api", "batch": i + 1, "email": rid, "error": err.get("message", "unknown")}
                    )
            log.info("batch %d/%d ok (%d results, %d errors)",
                     i + 1, len(batches), len(resp.get("results", [])), len(resp.get("errors", [])))
        checkpoint.mark(i)

    if not dry_run:
        checkpoint.clear()  # full pass finished: next run starts fresh
    summary.finished_at = _now()
    summary.duration_seconds = round(time.monotonic() - start, 2)
    _write_outputs(summary, out_dir)
    return summary


def _write_outputs(summary: RunSummary, out_dir: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out_dir / f"run-{stamp}.json").write_text(json.dumps(asdict(summary), indent=2, ensure_ascii=False))
    if summary.failures:
        with (out_dir / f"failed-{stamp}.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["stage", "batch", "row", "email", "error"])
            writer.writeheader()
            for f in summary.failures:
                writer.writerow({k: f.get(k, "") for k in writer.fieldnames})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
