"""Tests for account equity resolution and position sizing."""

from datetime import date

from app.account import (
    DEFAULT_EQUITY,
    get_settings,
    positions_value,
    resolve_equity,
    suggested_shares,
)
from app.models import Holding, Signal
from app.timeutil import utcnow


def add_holding(db, ticker, shares):
    db.add(Holding(ticker=ticker, shares=shares, added_at=utcnow()))
    db.commit()


def add_signal(db, ticker, close, stop_distance=2.0):
    db.add(
        Signal(
            ticker=ticker,
            as_of_date=date(2026, 8, 11),
            close=close,
            stop_distance=stop_distance,
            entry_signal=False,
            exit_signal=False,
            updated_at=utcnow(),
        )
    )
    db.commit()


def test_empty_account_falls_back_to_default(db):
    """A fresh install has nothing to derive equity from. It must fall back
    rather than size everything off zero, and must report that it did."""
    equity, source = resolve_equity(db)
    assert equity == DEFAULT_EQUITY
    assert source == "default"


def test_equity_derives_from_holdings_and_cash(db):
    add_holding(db, "GLD", 50)
    add_signal(db, "GLD", close=400.0)
    add_holding(db, "AAPL", 20)
    add_signal(db, "AAPL", close=300.0)

    settings = get_settings(db)
    settings.cash_balance = 5_000.0
    db.commit()

    equity, source = resolve_equity(db)
    assert equity == 50 * 400.0 + 20 * 300.0 + 5_000.0  # 31,000
    assert source == "derived"


def test_override_beats_derived(db):
    add_holding(db, "GLD", 50)
    add_signal(db, "GLD", close=400.0)

    settings = get_settings(db)
    settings.equity_override = 250_000.0
    db.commit()

    equity, source = resolve_equity(db)
    assert equity == 250_000.0
    assert source == "override"


def test_cash_alone_is_enough_to_derive(db):
    """Holding nothing but sitting on cash is a real state — it shouldn't
    drop to the placeholder."""
    settings = get_settings(db)
    settings.cash_balance = 20_000.0
    db.commit()

    equity, source = resolve_equity(db)
    assert equity == 20_000.0
    assert source == "derived"


def test_unvaluable_holdings_are_counted_not_silently_zeroed(db):
    """A holding with no share count, or one the pipeline hasn't priced
    yet, can't contribute to equity. It must be reported so the UI can say
    the total is incomplete rather than presenting it as the whole
    account."""
    add_holding(db, "GLD", 10)
    add_signal(db, "GLD", close=400.0)
    add_holding(db, "NOSHARES", None)  # added without a share count
    add_signal(db, "NOSHARES", close=50.0)
    add_holding(db, "UNPRICED", 5)  # no signal row at all

    value, unvalued = positions_value(db)
    assert value == 4_000.0
    assert unvalued == 2


def test_suggested_shares_is_risk_budget_over_stop_distance():
    assert suggested_shares(stop_distance=10.0, equity=100_000, risk_fraction=0.01) == 100.0
    assert suggested_shares(stop_distance=10.0, equity=200_000, risk_fraction=0.01) == 200.0
    assert suggested_shares(stop_distance=10.0, equity=100_000, risk_fraction=0.02) == 200.0


def test_suggested_shares_undefined_without_a_stop_distance():
    """A ticker with too little history has no ATR and therefore no stop
    distance. Dividing by it would be meaningless (or a ZeroDivisionError),
    so the size is None."""
    assert suggested_shares(None, 100_000, 0.01) is None
    assert suggested_shares(0.0, 100_000, 0.01) is None
    assert suggested_shares(-1.0, 100_000, 0.01) is None


def test_get_settings_creates_the_row_once(db):
    """Callers shouldn't have to handle a missing settings row, and two
    calls must not create two rows."""
    first = get_settings(db)
    first.cash_balance = 123.0
    db.commit()

    second = get_settings(db)
    assert second.id == first.id
    assert second.cash_balance == 123.0
