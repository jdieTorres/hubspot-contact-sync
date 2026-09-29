"""Clean, validate and map source rows to HubSpot contact properties before sending."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# source column -> HubSpot internal property name
DEFAULT_FIELD_MAP = {
    "email": "email",
    "first_name": "firstname",
    "last_name": "lastname",
    "phone": "phone",
    "company": "company",
    "city": "city",
    "language": "hs_language",
}

LANGUAGE_MAP = {"es": "es", "spanish": "es", "español": "es", "espanol": "es",
                "en": "en", "english": "en", "ingles": "en", "inglés": "en"}


@dataclass
class TransformResult:
    valid: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)  # {"row": n, "email": ..., "error": ...}
    duplicates_merged: int = 0


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def normalize_name(value: str | None) -> str:
    value = " ".join((value or "").split())
    return value.title() if value.isupper() or value.islower() else value


def normalize_phone(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    digits = re.sub(r"[^\d+]", "", value)
    return digits


def normalize_language(value: str | None) -> str:
    return LANGUAGE_MAP.get((value or "").strip().lower(), "")


def transform_rows(rows: list[dict], field_map: dict[str, str] | None = None) -> TransformResult:
    """Validate and normalize rows; merge duplicates by email (later rows win, blanks don't overwrite)."""
    field_map = field_map or DEFAULT_FIELD_MAP
    result = TransformResult()
    by_email: dict[str, dict] = {}

    for idx, row in enumerate(rows, start=2):  # row 1 is the CSV header
        email = normalize_email(row.get("email"))
        if not email:
            result.rejected.append({"row": idx, "email": "", "error": "missing email"})
            continue
        if not EMAIL_RE.match(email):
            result.rejected.append({"row": idx, "email": email, "error": "invalid email format"})
            continue

        props: dict[str, str] = {}
        for src, dest in field_map.items():
            raw = row.get(src)
            if src == "email":
                value = email
            elif src in ("first_name", "last_name"):
                value = normalize_name(raw)
            elif src == "phone":
                value = normalize_phone(raw)
            elif src == "language":
                value = normalize_language(raw)
            else:
                value = (raw or "").strip()
            if value:
                props[dest] = value

        if email in by_email:
            result.duplicates_merged += 1
            by_email[email].update(props)  # blanks were skipped, so they never erase data
        else:
            by_email[email] = props

    result.valid = list(by_email.values())
    return result
