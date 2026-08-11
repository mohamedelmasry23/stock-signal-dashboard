"""
One source of truth for "now", so timestamp handling can't drift between
the pipeline, the seeder, the importer, and the API.

Why naive-UTC rather than timezone-aware:

`datetime.utcnow()` is deprecated from Python 3.12 and its replacement,
`datetime.now(UTC)`, returns a timezone-AWARE value. That distinction
turns out not to reach the database — SQLAlchemy's SQLite dialect
formats DateTime columns without an offset and reads them back naive,
even when the column is declared `DateTime(timezone=True)`. So an aware
value written today is stored exactly like the naive rows already in
`real.db`/`demo.db`, and comes back naive regardless.

Storing naive UTC is therefore what actually happens no matter what we
pass in. Making that explicit here — rather than passing aware values
that get silently flattened — keeps in-memory objects and freshly-loaded
rows the same type, which is what prevents the classic
"can't compare offset-naive and offset-aware datetimes" TypeError.

The flip side is that a naive timestamp is ambiguous once it leaves the
API: JavaScript's `new Date("2026-08-11T18:50:26")` interprets a string
with no offset as LOCAL time, so a UTC timestamp would land hours off
and any "signals are stale" check in the frontend would be wrong. That's
fixed at the serialization boundary instead — see UtcDateTime in
app/schemas.py, which stamps an explicit +00:00 on the way out.
"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Current UTC time as a naive datetime — the storage convention for
    every DateTime column in this app. See module docstring for why."""
    return datetime.now(UTC).replace(tzinfo=None)
