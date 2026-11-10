from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, String, Text

from .database import Base
from .timeutil import utcnow


class Holding(Base):
    """An asset the user currently holds, entered manually.

    `shares` is whatever unit the ticker is quoted in — shares for a stock
    or ETF, troy ounces for a per-ounce commodity like GC=F — so that
    `shares * close` is always the position's dollar value.
    """

    __tablename__ = "holdings"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, unique=True, index=True, nullable=False)
    shares = Column(Float, nullable=True)
    added_at = Column(DateTime, default=utcnow)


class BacktestRun(Base):
    """A completed backtest, stored so the dashboard can display it.

    Backtest *results* belong in the app database because the API serves
    them. The raw historical bars they were computed from do not — those
    live in a separate cache file (see app/backtest/prices.py), or every
    API start would create an empty bars table in here.
    """

    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    label = Column(String)
    universe = Column(String)
    # Which system was measured (currently always "donchian"). Part of the
    # identity of a run: `save_result` replaces the previous run matching
    # strategy+variant+universe.
    #
    # Nullable because rows written before this column existed have no
    # value; readers treat NULL as "donchian", which is what those rows are.
    strategy = Column(String, index=True, default="donchian", nullable=True)
    # "faithful" (strategy's own exit only, as the dashboard implements) or
    # "atr_stop" (the comparison run with a hard stop).
    variant = Column(String, index=True)
    start_date = Column(Date)
    end_date = Column(Date)
    config_json = Column(Text)
    summary_json = Column(Text)
    benchmark_summary_json = Column(Text, nullable=True)


class BacktestEquityPoint(Base):
    """One point on a stored equity curve. Downsampled before storage —
    a decade of daily bars is far more resolution than a dashboard chart
    can render, and storing every point would bloat the database the API
    reads on every request."""

    __tablename__ = "backtest_equity"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, index=True, nullable=False)
    as_of_date = Column(Date, nullable=False)
    strategy = Column(Float)
    benchmark = Column(Float, nullable=True)
    # Fraction of equity in positions on this bar, 0..1. Stored because a
    # bare equity curve cannot distinguish "held on and went nowhere" from
    # "was in cash" — a distinction that is the whole story for a strategy
    # invested under a fifth of the time. Null on runs predating it.
    exposure = Column(Float, nullable=True)


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, index=True, nullable=False)
    ticker = Column(String)
    entry_date = Column(Date)
    entry_price = Column(Float)
    shares = Column(Float)
    exit_date = Column(Date)
    exit_price = Column(Float)
    exit_reason = Column(String)
    pnl = Column(Float)
    pnl_pct = Column(Float)
    r_multiple = Column(Float)
    bars_held = Column(Integer)


class AccountSettings(Base):
    """
    Single-row table (id=1) holding the account-level inputs to position
    sizing. These are deliberately separate from `signals`: a signal is a
    fact about the market and only changes when prices do, whereas how
    many shares to buy depends on how much money you have — which changes
    without any price moving. Keeping them apart is why editing cash here
    re-sizes every suggestion immediately, rather than waiting for the
    next pipeline run.
    """

    __tablename__ = "account_settings"

    id = Column(Integer, primary_key=True, default=1)
    # Uninvested cash. Holdings alone understate the account: sizing off
    # positions-only would shrink your risk budget precisely when you've
    # sold down and hold mostly cash.
    cash_balance = Column(Float, default=0.0, nullable=False)
    # Fraction of equity risked per position (the strategy's 1% default).
    risk_fraction = Column(Float, default=0.01, nullable=False)
    # Explicit total account value. Wins over the derived figure — for when
    # you'd rather state the number than have it inferred from holdings.
    equity_override = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=utcnow)


class Universe(Base):
    """The scan universe: S&P 500 constituents + commodity ETFs."""

    __tablename__ = "universe"

    ticker = Column(String, primary_key=True)
    asset_class = Column(String, default="equity")  # "equity" | "commodity"
    active = Column(Boolean, default=True)


class Signal(Base):
    """
    Latest computed Donchian/ATR signal snapshot per ticker.
    Overwritten daily by the pipeline (once it exists) shortly after
    market close — this table always reflects "as of last close".
    """

    __tablename__ = "signals"

    ticker = Column(String, primary_key=True)
    as_of_date = Column(Date)
    close = Column(Float)
    # The prior session's close, stored so the day's move can be shown
    # without refetching history. Null when a ticker has only one bar of
    # data — a brand-new listing has no previous close, and reporting 0%
    # would be a claim rather than an absence.
    prev_close = Column(Float, nullable=True)
    donchian_high = Column(Float, nullable=True)   # N-day (55) entry level
    donchian_low = Column(Float, nullable=True)    # M-day (20) exit level
    atr = Column(Float, nullable=True)
    stop_distance = Column(Float, nullable=True)
    entry_signal = Column(Boolean, default=False)
    exit_signal = Column(Boolean, default=False)
    suggested_shares = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=utcnow)


class WatchItem(Base):
    """One ticker trading above its 55-day Donchian high right now.

    NOT a signal, and deliberately not in `signals`. A row here says only
    "price is above the level a close would have to beat". It can appear
    at 11:00, fade, close below and never be flagged — that is the
    end-of-day rule working, not a miss. `signals` stays end-of-day and
    authoritative because that is what the backtest describes; nothing in
    this table has a backtest behind it.

    Rewritten wholesale by each scan, the way the pipeline rewrites
    `universe`. Row count is bounded by the scan universe (~547, with 275
    the worst case observed in ten years of history), so it never grows.

    On the rule that raw bars don't belong in this database (see
    BacktestRun below): that rule is about unbounded, undifferentiated
    *inputs*. A watch row is the same kind of object as a Signal row — one
    derived observation per ticker, computed by a scheduled job, served
    directly by the API. The bars it came from are discarded in the
    function that computed it.

    `level` is recomputed per scan and is NOT `signals.donchian_high`,
    which is one bar stale for the session in progress. See app/watch.py.
    """

    __tablename__ = "watch_items"

    ticker = Column(String, primary_key=True)
    price = Column(Float, nullable=False)  # the in-progress bar's current price
    level = Column(Float, nullable=False)  # 55-day high incl. the last completed session
    atr = Column(Float, nullable=True)  # from completed bars only
    atrs_above = Column(Float, nullable=True)  # (price - level) / atr — the ranking key
    prev_close = Column(Float, nullable=True)  # last COMPLETED close, for the day's move
    # Opened already above the level, rather than crossing during the
    # session. 22.7% of historical breakouts, and MRNA's case.
    gap_open = Column(Boolean, default=False)


class WatchScan(Base):
    """Single row (id=1): when the watchlist was last built, and how well.

    Separate from `watch_items` because the freshness stamp has to survive
    an empty result. Zero rows and no stamp is indistinguishable from
    "never scanned"; zero rows stamped four minutes ago is a real and
    useful answer — nothing is above its level right now.

    `scanned_at` lives here rather than on each item because every item
    comes from one atomic scan, so a per-row stamp would be the same value
    repeated N times and could only ever diverge through a bug.
    """

    __tablename__ = "watch_scan"

    id = Column(Integer, primary_key=True, default=1)
    scanned_at = Column(DateTime, nullable=True)  # naive UTC, per app/timeutil.py
    session_date = Column(Date, nullable=True)  # the ET calendar date this scan covers
    universe_size = Column(Integer, default=0)  # tickers asked for
    fetched = Column(Integer, default=0)  # tickers that came back with a today-bar
    market_state = Column(String, nullable=True)  # "open" | "closed"
