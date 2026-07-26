"""
Google News gives you a time.struct_time, Yahoo Finance gives you either an
ISO string or an epoch int depending on the field, and SEC RSS gives you an
ISO-ish string. Normalize all of it to ISO 8601 UTC strings so every document
in CouchDB has timestamps in one consistent shape.
"""
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    if isinstance(value, str):
        return value  # assume already ISO-ish; stored as-is, not re-parsed
    return str(value)
