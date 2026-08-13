"""
Tests for point-in-time universe files.

These are offline: the Wayback fetch itself isn't tested (it depends on a
third-party archive), but everything that consumes its output is — which
is where a mistake would silently change which companies a backtest sees.
"""

from pathlib import Path

from app.backtest.__main__ import resolve_universe
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
