from datetime import UTC, date, datetime
from typing import Annotated, List, Optional

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


class QuoteOut(BaseModel):
    """A live quote. Present only when the caller asked for one and Yahoo
    answered — null means the UI should fall back to the stored
    end-of-day figures, because a quote is an enhancement and never a
    requirement for the dashboard to work."""

    price: Optional[float] = None
    # Yahoo's own regularMarketChangePercent, already scaled as a
    # percentage: 0.53 means 0.53%, not 53%.
    change_percent: Optional[float] = None
    previous_close: Optional[float] = None
    # REGULAR | CLOSED | PRE | POST — lets the UI say whether this is a
    # live figure or simply the last session's final move.
    market_state: Optional[str] = None


class HoldingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticker: str
    shares: Optional[float] = None
    added_at: UtcDateTime
    signal: Optional[SignalOut] = None
    quote: Optional[QuoteOut] = None
    # shares x whichever price is authoritative (live if available, else
    # the stored close). Computed here so the frontend never has to decide
    # which price to value a position at.
    market_value: Optional[float] = None


class EntryCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticker: str
    close: float
    donchian_high: Optional[float] = None
    atr: Optional[float] = None
    suggested_shares: Optional[float] = None
    breakout_strength: Optional[float] = None  # (close - donchian_high) / ATR
    as_of_date: Optional[date] = None


class WatchItemOut(BaseModel):
    """One ticker trading above its 55-day high right now.

    Deliberately missing `suggested_shares`, which every other candidate
    model here carries. A position size asserts that something is
    tradeable, and nothing on this endpoint has been measured — its
    absence is the schema saying so.
    """

    model_config = ConfigDict(from_attributes=True)

    ticker: str
    price: float
    # Recomputed per scan, NOT signals.donchian_high, which is one bar
    # stale for the session in progress. See app/watch.py.
    level: float
    atr: Optional[float] = None
    atrs_above: Optional[float] = None  # (price - level) / atr — the ranking key
    prev_close: Optional[float] = None
    # Derived here from price/prev_close, unlike QuoteOut.change_percent,
    # which is Yahoo's own already-scaled field. Same name, different
    # provenance — read the Yahoo note in CLAUDE.md before touching either.
    change_percent: Optional[float] = None
    gap_open: bool = False
    held: bool = False
    already_flagged: bool = False  # the stored end-of-day signal is already true


class WatchOut(BaseModel):
    """The live watchlist, and everything needed to say how much to trust it.

    An envelope rather than a bare list, because the freshness fields are
    what make the list safe to render at all — a list alone could be shown
    as live at any hour of any day.
    """

    items: List[WatchItemOut] = []
    scanned_at: Optional[UtcDateTime] = None
    session_date: Optional[date] = None
    market_open: bool = False
    stale: bool = True
    universe_size: int = 0
    fetched: int = 0
    above_count: int = 0  # total above the level, before `limit` truncates
    truncated: bool = False


class TickerBarsOut(BaseModel):
    """Daily bars for the detail chart, stored columnar rather than as a
    list of bar objects.

    Ten years is roughly 2,500 bars, and repeating eight key names on every
    one of them costs more than the numbers themselves — around 400 KB
    against 175 KB. The chart also builds its SVG paths by walking indices,
    so this is the shape it actually consumes; a list of objects would only
    be transposed back in the browser.

    Every numeric column is null, never zero, wherever the value is
    undefined — through the 55-bar Donchian warm-up, most obviously. The
    path builder lifts the pen on null (see toPath in EquityChart.tsx), so
    a gap breaks the line rather than diving to the axis.
    """

    dates: List[date]
    open: List[Optional[float]]
    high: List[Optional[float]]
    low: List[Optional[float]]
    close: List[Optional[float]]
    volume: List[Optional[float]]
    donchian_high: List[Optional[float]]  # 55-day breakout level
    donchian_low: List[Optional[float]]   # 20-day exit level
    # Indices into `dates` of the bars where a signal *fired* — the crossing,
    # not every subsequent bar the condition stayed true. Price that breaks
    # its 55-day high tends to stay above it, so the state would put a marker
    # on most bars of a trend and answer "is it above the level" instead of
    # "where would you have bought".
    #
    # Indices rather than parallel booleans because these are sparse: a
    # decade holds a few dozen, so booleans would be 5,000 mostly-false
    # values to carry them.
    entry_index: List[int]
    exit_index: List[int]


class TickerStatsOut(BaseModel):
    """The headline numbers for a ticker, in the units the strategy uses.

    `signal_source` says where the levels came from: "pipeline" means the
    stored end-of-day row, the same figures the dashboard card shows;
    "computed" means this ticker isn't in the scan universe and the levels
    were derived here from its last completed bar. The distinction matters
    because only the first has been through a full daily run.
    """

    ticker: str
    long_name: Optional[str] = None
    asset_class: Optional[str] = None  # etf | commodity | equity, when known
    as_of_date: Optional[date] = None
    close: Optional[float] = None
    prev_close: Optional[float] = None
    donchian_high: Optional[float] = None
    donchian_low: Optional[float] = None
    atr: Optional[float] = None
    stop_distance: Optional[float] = None
    entry_signal: bool = False
    exit_signal: bool = False
    # (close - donchian_low) / atr — how much cushion is left before the
    # exit fires, in the same units the stop is sized in. Comparable across
    # tickers in a way a percentage isn't, because it's scaled by each
    # ticker's own volatility.
    atrs_to_exit: Optional[float] = None
    breakout_strength: Optional[float] = None  # (close - donchian_high) / atr
    suggested_shares: Optional[float] = None
    signal_source: str


class TickerDetailOut(BaseModel):
    """Everything the ticker detail page renders, in one round trip."""

    ticker: str
    range: str
    bars: TickerBarsOut
    stats: TickerStatsOut
    quote: Optional[QuoteOut] = None
    held: bool = False
    shares: Optional[float] = None
    market_value: Optional[float] = None
    # Yahoo's daily history includes the session in progress. The frontend
    # dims that candle and labels it, because it is not a bar yet.
    partial_last_bar: bool = False
    # False when Yahoo returned nothing but the ticker is known here. The
    # page still renders its stats from the stored row; a 404 would claim
    # the ticker doesn't exist, which is a much stronger thing to say than
    # "the price feed is down".
    history_available: bool = True


class RsiSignalOut(BaseModel):
    """The RSI(2) pullback equivalent of SignalOut. Same envelope, same
    meaning for `stop_distance`/`suggested_shares`; different indicators,
    because it's a different strategy rather than a variant of the same
    one."""

    model_config = ConfigDict(from_attributes=True)

    ticker: str
    as_of_date: Optional[date] = None
    close: Optional[float] = None
    prev_close: Optional[float] = None
    rsi: Optional[float] = None
    sma_fast: Optional[float] = None   # 5-day average — the exit level
    sma_trend: Optional[float] = None  # 200-day average — the entry filter
    atr: Optional[float] = None
    stop_distance: Optional[float] = None
    entry_signal: bool
    exit_signal: bool
    suggested_shares: Optional[float] = None
    updated_at: Optional[UtcDateTime] = None


class RsiHoldingOut(BaseModel):
    """A holding as the RSI page sees it. Identical to HoldingOut except
    that `signal` carries RSI fields — the position is the same position,
    but which of your holdings is flagged for exit depends entirely on
    which strategy is asking."""

    model_config = ConfigDict(from_attributes=True)

    ticker: str
    shares: Optional[float] = None
    added_at: UtcDateTime
    signal: Optional[RsiSignalOut] = None
    quote: Optional[QuoteOut] = None
    market_value: Optional[float] = None


class RsiEntryCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticker: str
    close: float
    rsi: Optional[float] = None
    sma_fast: Optional[float] = None
    sma_trend: Optional[float] = None
    atr: Optional[float] = None
    suggested_shares: Optional[float] = None
    # RSI points below the entry trigger — the ranking key, mirroring the
    # role `breakout_strength` plays for Donchian.
    oversold_depth: Optional[float] = None
    as_of_date: Optional[date] = None


class StatusOut(BaseModel):
    demo_mode: bool
    signals_last_updated: Optional[UtcDateTime] = None
    # Written by the same pipeline run, but tracked separately so the RSI
    # page reports its own freshness rather than borrowing Donchian's —
    # they diverge whenever one strategy's pass fails and the other's
    # doesn't.
    rsi_signals_last_updated: Optional[UtcDateTime] = None


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
    # True when this equity figure was computed from live quotes rather
    # than the last stored closes, so the UI can say which it is.
    live: bool = False


class EquityPointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    strategy: Optional[float] = None
    benchmark: Optional[float] = None
    # Share of equity in positions on this bar, 0..1. Null on runs stored
    # before it was recorded.
    exposure: Optional[float] = None


class BacktestRunOut(BaseModel):
    """A stored backtest. `summary` and `benchmark_summary` are the flat
    dicts produced by app/backtest/metrics.py — passed through as-is rather
    than re-modelled, so adding a metric doesn't require touching the
    schema, the API and the frontend types in lockstep."""

    id: int
    label: str
    universe: str
    strategy: str  # "donchian" | "rsi"
    variant: str  # "faithful" | "atr_stop"
    start_date: date
    end_date: date
    summary: dict
    benchmark_summary: Optional[dict] = None
    equity: List[EquityPointOut] = []


class BacktestOut(BaseModel):
    """Both variants of the most recent run, plus the caveats that must
    travel with any number quoted from them.

    `point_in_time` is the same faithful strategy measured over a frozen
    historical index membership instead of today's. It's a separate field
    rather than a third variant because it differs in universe, not in
    rules — and the dashboard draws it against `faithful` to show how wide
    the survivorship-bias bracket really is."""

    faithful: Optional[BacktestRunOut] = None
    atr_stop: Optional[BacktestRunOut] = None
    point_in_time: Optional[BacktestRunOut] = None
    caveats: List[str] = []


class AccountUpdate(BaseModel):
    """All fields optional — PATCH semantics. `equity_override` is
    explicitly nullable so it can be cleared back to the derived figure;
    that's why the sentinel dance in the endpoint is necessary."""

    cash_balance: Optional[float] = Field(default=None, ge=0)
    risk_fraction: Optional[float] = Field(default=None, gt=0, le=1)
    equity_override: Optional[float] = Field(default=None, ge=0)
