"""Minimal HubSpot CRM client: auth, retries with backoff, pagination and batch upsert."""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Iterator

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.hubapi.com"
BATCH_LIMIT = 100  # HubSpot batch endpoints accept at most 100 records per call
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class HubSpotError(Exception):
    """Non-retryable error (or retries exhausted) returned by the HubSpot API."""

    def __init__(self, status: int, message: str, payload: Any = None):
        super().__init__(f"HubSpot API {status}: {message}")
        self.status = status
        self.payload = payload


class HubSpotClient:
    def __init__(
        self,
        token: str,
        base_url: str = BASE_URL,
        max_retries: int = 5,
        backoff_base: float = 1.0,
        timeout: float = 30.0,
        session: requests.Session | None = None,
        sleep=time.sleep,
    ):
        if not token:
            raise ValueError("A private app access token is required (HUBSPOT_TOKEN).")
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.timeout = timeout
        self._sleep = sleep
        self.session = session or requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        )

    # ------------------------------------------------------------------ core
    def request(self, method: str, path: str, **kwargs) -> dict:
        """Send a request, retrying on 429/5xx and network errors with exponential backoff."""
        url = f"{self.base_url}{path}"
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == self.max_retries:
                    raise HubSpotError(0, f"network error: {exc}") from exc
                self._wait(attempt, None, reason=f"network error ({exc.__class__.__name__})")
                continue

            if resp.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                self._wait(attempt, resp.headers.get("Retry-After"), reason=f"HTTP {resp.status_code}")
                continue

            if resp.status_code >= 400:
                payload = _safe_json(resp)
                message = payload.get("message", resp.text[:300]) if isinstance(payload, dict) else resp.text[:300]
                raise HubSpotError(resp.status_code, message, payload)

            return _safe_json(resp) or {}
        raise HubSpotError(0, "retries exhausted")  # pragma: no cover

    def _wait(self, attempt: int, retry_after: str | None, reason: str) -> None:
        if retry_after is not None:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = self.backoff_base * 2**attempt
        else:
            # exponential backoff with jitter: 1s, 2s, 4s, 8s... (+ up to 0.5s)
            delay = self.backoff_base * 2**attempt + random.uniform(0, 0.5)
        log.warning("%s — retry %d/%d in %.1fs", reason, attempt + 1, self.max_retries, delay)
        self._sleep(delay)

    # --------------------------------------------------------------- contacts
    def iter_contacts(self, properties: list[str] | None = None, limit: int = 100) -> Iterator[dict]:
        """Yield every contact, following the `paging.next.after` cursor."""
        params: dict[str, Any] = {"limit": limit}
        if properties:
            params["properties"] = ",".join(properties)
        while True:
            data = self.request("GET", "/crm/v3/objects/contacts", params=params)
            yield from data.get("results", [])
            after = data.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
            params["after"] = after

    def batch_upsert_contacts(self, records: list[dict], id_property: str = "email") -> dict:
        """Create-or-update up to 100 contacts in one call, keyed by a unique property.

        Upsert makes the sync idempotent: re-running the same batch never creates duplicates.
        HubSpot answers 200 (all ok) or 207 (multi-status: some records failed).
        """
        if len(records) > BATCH_LIMIT:
            raise ValueError(f"batch size {len(records)} exceeds HubSpot limit of {BATCH_LIMIT}")
        inputs = [
            {"idProperty": id_property, "id": r[id_property], "properties": r}
            for r in records
        ]
        return self.request(
            "POST", "/crm/v3/objects/contacts/batch/upsert", json={"inputs": inputs}
        )


def _safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None
