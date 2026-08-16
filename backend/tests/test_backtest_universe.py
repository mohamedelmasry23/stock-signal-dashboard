"""
Tests for point-in-time universe files.

These are offline: the Wayback fetch itself isn't tested (it depends on a
third-party archive), but everything that consumes its output is — which
is where a mistake would silently change which companies a backtest sees.
"""

from pathlib import Path

from app.backtest.__main__ import resolve_costs, resolve_universe, universe_label
from app.pipeline import rsi_scan_universe
from app.config import is_point_in_time
from app.backtest.universe import load_membership, save_membership


def test_load_membership_ignores_comments_and_blanks(tmp_path):
    path = tmp_path / "u.txt"
    path.write_text("# sp500_2016\n# 3 tickers\n# source: http://example\nAAPL\n\n  msft  \nBRK-B\n")

    assert load_membership(path) == ["AAPL", "BRK-B", "MSFT"]


def test_save_then_load_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr("app.backtest.universe.UNIVERSE_DIR", tmp_path)
    written = save_membership(["MSFT", "AAPL"], "test_list", source="http://example")

    assert load_membership(written) == ["AAPL", "MSFT"]
    # Provenance is written into the file: a universe list with no recorded
    # source can't be audited or regenerated later.
    assert "http://example" in written.read_text()


def test_resolve_universe_reads_a_file_spec(tmp_path):
    path = tmp_path / "u.txt"
    path.write_text("# header\nAAPL\nMSFT\n")

    tradeable, excluded = resolve_universe(f"file:{path}")

    assert tradeable == ["AAPL", "MSFT"]
    assert excluded == []


def test_resolve_universe_combines_a_file_with_named_groups(tmp_path):
    path = tmp_path / "u.txt"
    path.write_text("AAPL\n")

    tradeable, _ = resolve_universe(f"file:{path},etfs")

    assert "AAPL" in tradeable
    assert "SPUS" in tradeable  # from EXTRA_EQUITY_TICKERS


def test_futures_are_still_excluded_from_a_file_universe(tmp_path):
    """A saved list could contain a futures symbol; the roll-gap exclusion
    has to apply there too, not just to the built-in commodity group."""
    path = tmp_path / "u.txt"
    path.write_text("AAPL\nGC=F\n")

    tradeable, excluded = resolve_universe(f"file:{path}")

    assert tradeable == ["AAPL"]
    assert excluded == ["GC=F"]


def test_universe_label_collapses_file_paths_to_the_snapshot_name():
    """The raw spec carries a path that would be meaningless in the
    dashboard — and unstable, since it moves with the repo."""
    assert (
        universe_label("file:data/universes/sp500_2016.txt,etfs") == "sp500_2016,etfs"
    )
    assert universe_label("sp500,etfs") == "sp500,etfs"


def test_the_stored_label_is_what_marks_a_run_point_in_time():
    """These two have to agree: universe_label writes the label and
    is_point_in_time reads it, and between them they decide whether the
    dashboard calls a run optimistic or pessimistic."""
    assert is_point_in_time(universe_label("file:data/universes/sp500_2016.txt,etfs"))
    assert not is_point_in_time(universe_label("sp500,etfs"))
    assert not is_point_in_time(universe_label("commodities,etfs"))
    assert not is_point_in_time("")


def test_rsi_scans_baskets_plus_whatever_you_hold():
    """The RSI universe is deliberately narrower than the pipeline's, twice
    over: single companies lost badly (8.3% vs SPY's 353%), and the
    commodity funds cost Sharpe 0.61 -> 0.43. Holdings are unioned in
    regardless of type — anything you own must still get an exit signal,
    which is the one thing this page can never silently stop doing."""
    universe = rsi_scan_universe(held=["NVDA", "GLD"])

    assert "NVDA" in universe          # held single stock — managed, never suggested
    assert "GLD" in universe           # held commodity fund — same
    assert "XLF" in universe           # sector ETF
    assert "VOO" in universe           # broad index

    # Neither is scanned when it isn't held. AAPL pins the single-stock
    # narrowing, UGA the commodity one — without these the filters could
    # silently become no-ops.
    assert "AAPL" not in rsi_scan_universe(held=[])
    assert "UGA" not in rsi_scan_universe(held=[])
    assert "GLD" not in rsi_scan_universe(held=[])


def test_spy_is_not_tradeable_by_the_rsi_universe():
    """SPY is the benchmark, and `python -m app.backtest` pops the
    benchmark out of the tradeable set — so a page suggesting SPY would be
    recommending a ticker the backtest structurally cannot trade. VOO
    carries the same exposure and appears in both."""
    assert "SPY" not in rsi_scan_universe(held=[])
    assert "VOO" in rsi_scan_universe(held=[])


def test_index_etfs_resolve_as_a_backtest_universe():
    """The live scan and the backtest have to agree on what the universe
    IS, or the measurement describes a different system."""
    tradeable, excluded = resolve_universe("index_etfs,commodities")

    assert set(tradeable) >= {"VOO", "XLF", "TLT", "GLD"}
    assert "AAPL" not in tradeable
    assert excluded == ["GC=F"]  # futures roll gaps are not returns


def test_costs_default_to_the_universe_being_traded():
    """A single-stock rate applied to penny-spread index funds was the
    largest distortion in the RSI measurement — 8.4% at 10bps per side
    against 87.4% at a rate those instruments actually trade at. An
    explicit flag still wins."""
    assert resolve_costs("index_etfs") == (0.75, 0.75)
    assert resolve_costs("index_etfs,commodities") == (0.75, 0.75)
    assert resolve_costs("sp500,etfs") == (5.0, 5.0)
    # Explicit values are never overridden by the universe.
    assert resolve_costs("index_etfs", cost_bps=5.0, slippage_bps=5.0) == (5.0, 5.0)
    assert resolve_costs("sp500", cost_bps=0.0)[0] == 0.0
