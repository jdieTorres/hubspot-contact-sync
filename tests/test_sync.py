import csv
import json

import pytest

from hubspot_sync.client import HubSpotError
from hubspot_sync.sync import run_sync


class FakeClient:
    """Stands in for HubSpotClient; can fail a given batch number once."""

    def __init__(self, fail_on_call=None, partial_error_email=None):
        self.calls = []
        self.fail_on_call = fail_on_call
        self.partial_error_email = partial_error_email

    def batch_upsert_contacts(self, records):
        self.calls.append([r["email"] for r in records])
        if self.fail_on_call == len(self.calls):
            raise HubSpotError(400, "Property values were not valid")
        errors, results = [], []
        for r in records:
            if r["email"] == self.partial_error_email:
                errors.append({"message": "INVALID_EMAIL", "context": {"ids": [r["email"]]}})
            else:
                results.append({"id": r["email"], "new": r["email"].startswith("new")})
        return {"results": results, "errors": errors}


def write_csv(path, n, prefix="new"):
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["email", "first_name"])
        w.writeheader()
        for i in range(n):
            w.writerow({"email": f"{prefix}{i}@x.co", "first_name": f"user{i}"})


def test_batches_of_100_and_counts(tmp_path):
    src = tmp_path / "c.csv"
    write_csv(src, 250)
    client = FakeClient()
    s = run_sync(client, src, tmp_path / "out")
    assert [len(c) for c in client.calls] == [100, 100, 50]
    assert (s.created, s.updated, s.failed) == (250, 0, 0)
    assert s.ok


def test_failed_batch_is_logged_and_run_continues(tmp_path):
    src = tmp_path / "c.csv"
    write_csv(src, 250)
    client = FakeClient(fail_on_call=2)
    s = run_sync(client, src, tmp_path / "out")
    assert len(client.calls) == 3
    assert s.failed == 100 and s.created == 150
    failed_files = list((tmp_path / "out").glob("failed-*.csv"))
    assert len(failed_files) == 1
    assert len(failed_files[0].read_text().splitlines()) == 101  # header + 100 rows


def test_partial_207_errors_are_recorded(tmp_path):
    src = tmp_path / "c.csv"
    write_csv(src, 5)
    s = run_sync(FakeClient(partial_error_email="new3@x.co"), src, tmp_path / "out")
    assert s.created == 4 and s.failed == 1
    assert s.failures[0]["email"] == "new3@x.co"


def test_resumes_from_checkpoint_after_crash(tmp_path):
    src = tmp_path / "c.csv"
    write_csv(src, 300)
    out = tmp_path / "out"

    class Crash(Exception):
        pass

    class CrashingClient(FakeClient):
        def batch_upsert_contacts(self, records):
            if len(self.calls) == 2:
                raise Crash("process killed")
            return super().batch_upsert_contacts(records)

    with pytest.raises(Crash):
        run_sync(CrashingClient(), src, out)
    assert json.loads((out / "checkpoint.json").read_text())["last_batch_done"] == 1

    client = FakeClient()
    s = run_sync(client, src, out)
    assert s.batches_skipped_from_checkpoint == 2
    assert len(client.calls) == 1  # only the third batch is sent again
    assert not (out / "checkpoint.json").exists()


def test_checkpoint_ignored_if_source_file_changed(tmp_path):
    src = tmp_path / "c.csv"
    out = tmp_path / "out"
    out.mkdir()
    (out / "checkpoint.json").write_text(json.dumps({"fingerprint": "other", "last_batch_done": 5}))
    write_csv(src, 10)
    client = FakeClient()
    run_sync(client, src, out)
    assert len(client.calls) == 1


def test_dry_run_calls_nothing(tmp_path):
    src = tmp_path / "c.csv"
    write_csv(src, 120)
    s = run_sync(None, src, tmp_path / "out", dry_run=True)
    assert s.batches_total == 2 and s.created == 0
