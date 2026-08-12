from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, String

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
