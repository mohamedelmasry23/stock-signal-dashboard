"""
Account equity and position sizing.

The strategy sizes a position so that a stop-out costs a fixed fraction of
the account:

    shares = (risk_fraction x equity) / stop_distance

`stop_distance` (2 x ATR) is a market fact — the pipeline computes and
stores it. `equity` is not: it's how much money you have, which changes
when you deposit, withdraw or trade, with no price moving at all. So
equity is resolved here at read time rather than frozen into the signals
table, which is what lets a cash-balance edit re-size every suggestion on
the spot instead of waiting for the next daily run.

Equity is resolved in three tiers:

  1. `equity_override`, if set — you state the number explicitly.
  2. Otherwise the derived figure: market value of holdings (shares x last
     close) plus `cash_balance`.
  3. Otherwise DEFAULT_EQUITY — the fresh-install / demo fallback, so a
     brand-new database still shows plausible sizing rather than zeros.

Tier 2 is what makes this track your Wealthsimple CSV: the importer
rewrites `holdings` from the export, so the next read reflects whatever
you actually own.
"""

from typing import Optional

from sqlalchemy.orm import Session

from .models import AccountSettings, Holding, Signal

# Matches the original brief's fixed notional. Only used when there's
# nothing to derive equity from — see tier 3 above.
DEFAULT_EQUITY = 100_000.0

SETTINGS_ID = 1


def get_settings(db: Session) -> AccountSettings:
    """Fetches the single settings row, creating it with defaults on first
    use so callers never have to handle a missing row."""
    row = db.get(AccountSettings, SETTINGS_ID)
    if row is None:
        row = AccountSettings(id=SETTINGS_ID, cash_balance=0.0, risk_fraction=0.01)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def positions_value(db: Session) -> tuple[float, int]:
    """Market value of all holdings, plus a count of holdings that couldn't
    be valued. A holding is unvaluable when its share count is unset (the
    ticker was added without one) or the pipeline hasn't computed a close
    for it yet — both are normal states, not errors, but they mean the
    equity figure understates the account, so the count is surfaced to the
    UI rather than silently swallowed.
    """
    total = 0.0
    unvalued = 0

    rows = (
        db.query(Holding, Signal)
        .outerjoin(Signal, Signal.ticker == Holding.ticker)
        .all()
    )
    for holding, signal in rows:
        if holding.shares is None or signal is None or signal.close is None:
            unvalued += 1
            continue
        total += holding.shares * signal.close

    return total, unvalued


def resolve_equity(db: Session) -> tuple[float, str]:
    """Returns (equity, source) where source is one of "override",
    "derived" or "default" — the UI shows it so the number on screen is
    never an unexplained figure."""
    settings = get_settings(db)

    if settings.equity_override is not None and settings.equity_override > 0:
        return settings.equity_override, "override"

    value, _ = positions_value(db)
    derived = value + (settings.cash_balance or 0.0)
    if derived > 0:
        return derived, "derived"

    return DEFAULT_EQUITY, "default"


def suggested_shares(
    stop_distance: Optional[float],
    equity: float,
    risk_fraction: float,
) -> Optional[float]:
    """Position size at the configured risk. None when stop_distance is
    missing or non-positive — dividing by it would be meaningless, and a
    ticker with too little history for an ATR legitimately has neither."""
    if stop_distance is None or stop_distance <= 0:
        return None
    return (risk_fraction * equity) / stop_distance
