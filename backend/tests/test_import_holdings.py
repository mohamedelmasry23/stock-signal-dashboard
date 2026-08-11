"""
Tests for the Wealthsimple CSV import.

These matter more than most: the parser matches columns by alias because
Wealthsimple publishes no stable schema, and it treats each file as a full
transaction history. Both decisions are easy to break silently — a wrong
net position looks like a plausible number, not an error.
"""

import pytest

from app.import_holdings import parse_net_positions, resync_holdings
from app.models import Holding
from app.timeutil import utcnow


def write_csv(tmp_path, text):
    path = tmp_path / "activity.csv"
    path.write_text(text)
    return path


def test_nets_buys_against_sells(tmp_path):
    csv = write_csv(
        tmp_path,
        "Date,Transaction,Symbol,Quantity,Price\n"
        "2026-01-05,Buy,AAPL,10,180\n"
        "2026-02-10,Buy,AAPL,5,190\n"
        "2026-03-01,Sell,AAPL,3,200\n",
    )
    assert parse_net_positions(csv) == {"AAPL": 12.0}


def test_fully_sold_position_is_dropped(tmp_path):
    """Netting to zero must remove the ticker entirely, not leave a
    zero-share holding that would still show on the dashboard."""
    csv = write_csv(
        tmp_path,
        "Date,Transaction,Symbol,Quantity,Price\n"
        "2026-01-20,Buy,BX,4,350\n"
        "2026-05-01,Sell,BX,4,360\n",
    )
    assert parse_net_positions(csv) == {}


def test_non_trade_rows_are_ignored(tmp_path):
    """Dividends, deposits and fees appear in the same export but don't
    change a position."""
    csv = write_csv(
        tmp_path,
        "Date,Transaction,Symbol,Quantity,Price\n"
        "2026-01-05,Buy,GLD,10,190\n"
        "2026-02-20,Dividend,GLD,0,4.50\n"
        "2026-02-21,Deposit,GLD,500,1\n"
        "2026-02-22,Fee,GLD,1,1\n",
    )
    assert parse_net_positions(csv) == {"GLD": 10.0}


def test_share_class_dots_become_dashes(tmp_path):
    """Wealthsimple/Wikipedia write BRK.B; Yahoo needs BRK-B. A mismatch
    here means the holding never joins to a signal."""
    csv = write_csv(
        tmp_path,
        "Date,Transaction,Symbol,Quantity,Price\n2026-01-20,Buy,brk.b,2,350\n",
    )
    assert parse_net_positions(csv) == {"BRK-B": 2.0}


@pytest.mark.parametrize(
    "header",
    [
        "Date,Transaction,Symbol,Quantity,Price",
        "date,type,ticker,shares,price",
        "Transaction Date,Activity,Security,Units,Rate",
    ],
)
def test_column_aliases_are_matched(tmp_path, header):
    """Wealthsimple's exact headers aren't documented and differ by account
    type, so the parser matches on aliases after normalising case and
    punctuation. All three spellings must land on the same result."""
    csv = write_csv(tmp_path, f"{header}\n2026-01-05,Buy,GLD,10,190\n")
    assert parse_net_positions(csv) == {"GLD": 10.0}


def test_missing_column_names_the_problem(tmp_path):
    """The failure path is part of the feature: if a real export doesn't
    match, the message has to say which field is missing and what headers
    were actually seen, or there's no way to fix the alias list."""
    csv = write_csv(tmp_path, "Date,Price\n2026-01-05,190\n")

    with pytest.raises(ValueError) as excinfo:
        parse_net_positions(csv)

    message = str(excinfo.value)
    assert "ticker" in message
    assert "Date" in message and "Price" in message  # the observed headers


def test_resync_replaces_rather_than_merges(db):
    """Each import is a full resync. A ticker absent from the new file has
    been sold and must disappear — merging would leave stale positions
    accumulating forever."""
    db.add(Holding(ticker="OLD", shares=5, added_at=utcnow()))
    db.add(Holding(ticker="KEEP", shares=1, added_at=utcnow()))
    db.commit()

    resync_holdings(db, {"KEEP": 7.0, "NEW": 3.0})

    held = {h.ticker: h.shares for h in db.query(Holding).all()}
    assert held == {"KEEP": 7.0, "NEW": 3.0}


def test_resync_preserves_added_at_for_existing_holdings(db):
    """Re-importing shouldn't reset how long you've held something."""
    original = utcnow()
    db.add(Holding(ticker="GLD", shares=5, added_at=original))
    db.commit()

    resync_holdings(db, {"GLD": 9.0})

    row = db.query(Holding).filter(Holding.ticker == "GLD").one()
    assert row.shares == 9.0
    assert row.added_at == original
