from datetime import UTC, date, datetime
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, WithJsonSchema


def _stamp_utc(dt: datetime) -> str:
    """Timestamps are stored naive-UTC (see app/timeutil.py). Serializing
    them bare would emit '2026-08-11T18:50:26', which JavaScript's
    `new Date(...)` reads as LOCAL time — putting every timestamp hours
    off in the frontend and breaking any "signals are stale" check.
    Attaching the offset the values already implicitly carry makes them
    unambiguous to any client."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


# Nullability is expressed by the Optional[...] wrapper at each use site,
# not baked in here — otherwise a required field like HoldingOut.added_at
# would be advertised as nullable in the OpenAPI schema. WithJsonSchema
# keeps the `format: date-time` hint that PlainSerializer would otherwise
# flatten to a bare string, so generated frontend clients still parse it
# as a date.
UtcDateTime = Annotated[
    datetime,
    PlainSerializer(_stamp_utc, return_type=str),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]


# Yahoo-style symbols: letters, digits, '-'/'.' for share classes and
# exchange suffixes (BRK-B, SHOP.TO), '=' for futures and FX (GC=F), and
# '^' for indices (^GSPC). Length 1-12 covers every real symbol with room
# to spare. This can't tell you a ticker actually exists — only a price
# fetch can — but it stops the obvious typo, the pasted sentence, and the
# empty string from being stored as a holding that then silently never
# gets a signal.
#
# '^' is deliberately not the first character in the class (where it would
# mean negation) and '-' is last (where it's a literal, not a range).
TICKER_PATTERN = r"^[A-Za-z0-9.=^-]{1,12}$"


class HoldingCreate(BaseModel):
    ticker: str = Field(pattern=TICKER_PATTERN)
    shares: Optional[float] = Field(default=None, ge=0)


class HoldingUpdate(BaseModel):
    shares: Optional[float] = Field(default=None, ge=0)


class SignalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticker: str
    as_of_date: Optional[date] = None
    close: Optional[float] = None
    prev_close: Optional[float] = None
    donchian_high: Optional[float] = None
    donchian_low: Optional[float] = None
    atr: Optional[float] = None
    stop_distance: Optional[float] = None
    entry_signal: bool
    exit_signal: bool
    suggested_shares: Optional[float] = None
    updated_at: Optional[UtcDateTime] = None


class HoldingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticker: str
    shares: Optional[float] = None
    added_at: UtcDateTime
    signal: Optional[SignalOut] = None


class EntryCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticker: str
    close: float
    donchian_high: Optional[float] = None
    atr: Optional[float] = None
    suggested_shares: Optional[float] = None
    breakout_strength: Optional[float] = None  # (close - donchian_high) / ATR
    as_of_date: Optional[date] = None


class StatusOut(BaseModel):
    demo_mode: bool
    signals_last_updated: Optional[UtcDateTime] = None


class AccountOut(BaseModel):
    """The equity figure plus the pieces it was built from, so the UI can
    explain the number rather than just printing it."""

    equity: float
    equity_source: str  # "override" | "derived" | "default"
    positions_value: float
    cash_balance: float
    risk_fraction: float
    equity_override: Optional[float] = None
    risk_dollars: float  # risk_fraction * equity — the per-trade risk budget
    unvalued_holdings: int  # holdings with no share count or no computed close


class AccountUpdate(BaseModel):
    """All fields optional — PATCH semantics. `equity_override` is
    explicitly nullable so it can be cleared back to the derived figure;
    that's why the sentinel dance in the endpoint is necessary."""

    cash_balance: Optional[float] = Field(default=None, ge=0)
    risk_fraction: Optional[float] = Field(default=None, gt=0, le=1)
    equity_override: Optional[float] = Field(default=None, ge=0)
