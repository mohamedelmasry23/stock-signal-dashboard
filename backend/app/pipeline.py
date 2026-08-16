"""
Daily signal pipeline.

Scans the universe (S&P 500 constituents + extra equity ETFs + commodity
tickers) plus any currently-held tickers — so exit signals still work
even for something outside the default universe — computes Donchian/ATR
signals via the shared compute_signal_frame(), and upserts the results
into `signals`.

Run once, shortly after US market close: that's when today's close is
finalized and tomorrow's trigger levels are locked in (see the
"Signal timing model" note in the original brief). See README.md for
cron / Task Scheduler setup.

    python -m app.pipeline

Refuses to run with DEMO_MODE=true — this pipeline pulls real market
data and writes real computed signals; that shouldn't land in the fake
demo database. Use app.seed_demo for fake data instead.
"""

import io
import sys
import time
from typing import Callable, Dict, List, Optional

import pandas as pd
import requests
import yfinance as yf

from .account import get_settings, resolve_equity, suggested_shares
from .config import DATA_DIR, DEMO_MODE
from .database import Base, SessionLocal, engine, sync_new_columns
from .models import Holding, RsiSignal, Signal, Universe
from .signals import compute_rsi_signal_frame, compute_signal_frame
from .timeutil import utcnow

# Equity ETFs scanned alongside the S&P 500 constituents. The Wikipedia
# list only covers index members, so anything held or watched outside it
# goes here.
EXTRA_EQUITY_TICKERS = [
    "SPUS",  # SP Funds S&P 500 Sharia Industry Exclusions ETF
    "SPTE",  # SP Funds S&P Global Technology ETF
]

# The RSI(2) scan universe: liquid, index-tracking ETFs.
#
# Mean reversion is a bet that an unusually sharp drop partly reverses.
# That bet is far better founded on a basket than on one company: an index
# has no single point of failure, so a 5% fall is usually flow rather than
# news, whereas one stock down 5% may have a reason that doesn't reverse
# at all. Measured on this project's own data, RSI(2) over the S&P 500
# constituents returned 8.3% against SPY's 352.9% — see backend/README.md.
#
# Two deliberate exclusions:
#
# * **SPY**, because it is the benchmark. `python -m app.backtest` pops the
#   benchmark out of the tradeable set, so a backtest could never buy it —
#   and a live page recommending a ticker the backtest structurally cannot
#   trade would break the parity the ranking work exists to protect. VOO
#   carries the same exposure and is tradeable in both.
# * **Leveraged and inverse products** (SQQQ, TQQQ, SH…). Daily rebalancing
#   makes their returns path-dependent, so a "dip" in one is not the same
#   object the strategy was designed and measured on.
INDEX_ETF_TICKERS = [
    # broad US market
    "VOO",   # S&P 500 — stands in for SPY, which is the benchmark
    "QQQ",   # Nasdaq 100
    "IWM",   # Russell 2000 small cap
    "DIA",   # Dow 30
    "VTI",   # total US market
    "IJH",   # S&P mid cap 400
    "IJR",   # S&P small cap 600
    # US sectors (the SPDR Select Sector family — the classic
    # mean-reversion set, since a whole sector rarely gaps on one story)
    "XLB",   # materials
    "XLC",   # communication services
    "XLE",   # energy
    "XLF",   # financials
    "XLI",   # industrials
    "XLK",   # technology
    "XLP",   # consumer staples
    "XLRE",  # real estate
    "XLU",   # utilities
    "XLV",   # health care
    "XLY",   # consumer discretionary
    # style
    "VTV",   # large cap value
    "VUG",   # large cap growth
    # international
    "EFA",   # developed markets ex-US
    "EEM",   # emerging markets
    "VGK",   # Europe
    "EWJ",   # Japan
    "FXI",   # China large cap
    # bonds — different regime entirely, and they dip on their own schedule
    "TLT",   # 20+ year treasuries
    "IEF",   # 7-10 year treasuries
    "AGG",   # US aggregate
    "LQD",   # investment grade credit
    "HYG",   # high yield credit
]

# Commodity tickers, grouped by sub-class. These are the whole commodity
# side of the scan universe — the S&P 500 half comes from Wikipedia.
# Note USO/BNO (WTI vs Brent) and DBC's energy weighting are strongly
# correlated, so an oil breakout tends to surface several of these at
# once and can crowd the entry-candidates list; trim if that annoys you.
COMMODITY_TICKERS = [
    # precious metals
    "GLD",   # gold ETF, ~1/10 oz per share
    # COMEX gold futures, quoted in USD per TROY OUNCE. This is the entry
    # to use for bullion held by weight (e.g. a broker's
    # physically-backed gold): enter ounces in the `shares` field and
    # `shares x close` is the position's real dollar value. Holding it as
    # GLD instead would misprice it, since a GLD share is a fraction of an
    # ounce and that fraction shrinks as the fund's expenses accrue.
    "GC=F",
    "SLV",   # silver
    "PPLT",  # platinum
    "PALL",  # palladium
    # energy
    "USO",   # WTI crude
    "BNO",   # Brent crude
    "UNG",   # natural gas
    "UGA",   # gasoline
    # industrial metals
    "CPER",  # copper
    # agriculture
    "DBA",   # broad agriculture
    # broad basket
    "DBC",   # diversified commodity index
]
CHUNK_SIZE = 50
PERIOD = "2y"
PAUSE_BETWEEN_CHUNKS_SEC = 1.0

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
# Wikipedia's user-agent policy: identify the tool, not a browser spoof.
USER_AGENT = "StockSignalDashboard/1.0 (personal portfolio project)"

# yfinance keeps a SQLite timezone cache, by default in
# ~/Library/Caches/py-yfinance. A launchd agent could not open it: the
# scheduled run failed 275 of 517 tickers with
# OperationalError('unable to open database file') while the identical
# command run from a shell succeeded completely.
#
# Rather than chase macOS's exact objection to that directory, keep the
# cache inside the project, which the app already reads and writes. It
# also makes a checkout self-contained — no state hiding in a system
# cache directory that a cleanup would silently wipe.
YF_CACHE_DIR = DATA_DIR / "yf_cache"


def configure_yfinance_cache() -> None:
    """Point yfinance's tz cache at the project. Safe to call repeatedly."""
    try:
        YF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        yf.set_tz_cache_location(str(YF_CACHE_DIR))
    except Exception as e:  # a cache-location problem must not kill a run
        print(f"[pipeline] WARNING: couldn't set yfinance cache location ({e})")


def get_sp500_tickers() -> List[str]:
    """Current S&P 500 constituents from Wikipedia. Falls back to an
    empty list (with a warning) if the fetch fails — commodities and
    holdings still get scanned either way.

    Fetched via requests rather than handing the URL straight to
    pandas.read_html: Wikipedia rejects urllib's default user-agent with
    a 403, so a descriptive UA (their API etiquette policy) is required."""
    try:
        resp = requests.get(SP500_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
        tickers = tables[0]["Symbol"].tolist()
        # Yahoo uses '-' where Wikipedia uses '.' for share classes (e.g. BRK.B -> BRK-B)
        return [str(t).replace(".", "-") for t in tickers]
    except Exception as e:
        print(f"[pipeline] WARNING: couldn't fetch S&P 500 list ({e}); skipping equities.")
        return []


def get_extra_equity_tickers() -> List[str]:
    return list(EXTRA_EQUITY_TICKERS)


def get_commodity_tickers() -> List[str]:
    return list(COMMODITY_TICKERS)


def get_index_etf_tickers() -> List[str]:
    return list(INDEX_ETF_TICKERS)


def rsi_scan_universe(held: List[str]) -> List[str]:
    """What the RSI(2) strategy is allowed to look at.

    Liquid index, sector, style and bond ETFs — never individual companies,
    and no longer the commodity funds either. Both exclusions were measured
    rather than assumed:

    * **Single stocks**: 8.3% over 2016-2026 against SPY's 353%.
    * **Commodity funds**: keeping PALL, UGA, CPER and BNO in cost Sharpe
      0.43 and a -12.5% drawdown, against **0.61 and -9.2%** without them.
      They are far thinner than the index ETFs, so they carry real spread
      the model understates, and they lost money on balance anyway.

    Held tickers are unioned in regardless of type, so a position you own
    always gets an exit signal — the one thing this page must never
    silently stop doing. A held commodity fund or single stock therefore
    still appears here to be managed, but can never be suggested as a new
    buy: `/api/rsi/entry-candidates` excludes anything already held.
    """
    return sorted(set(INDEX_ETF_TICKERS) | set(held))


def fetch_ohlc_batch(tickers: List[str], period: str = PERIOD) -> Dict[str, pd.DataFrame]:
    """Downloads OHLC history for a batch of tickers in one yfinance call
    and reshapes into {ticker: OHLC frame}, matching what
    compute_signal_frame() expects — same shape as the original
    single-ticker brief script, just handling more than one ticker."""
    if not tickers:
        return {}

    raw = yf.download(
        tickers, period=period, interval="1d",
        auto_adjust=False, progress=False, group_by="column",
    )

    result = {}
    for t in tickers:
        try:
            frame = pd.DataFrame({
                "Open": raw["Open"][t],
                "High": raw["High"][t],
                "Low": raw["Low"][t],
                "Close": raw["Close"][t],
                "Volume": raw["Volume"][t],
            }).dropna()
            if not frame.empty:
                result[t] = frame
        except Exception:
            continue  # delisted / bad ticker / no data — skip, don't kill the run
    return result


def previous_close(frame: pd.DataFrame) -> Optional[float]:
    """Second-to-last close, for showing the day's move. None when there's
    only one bar — a listing with no prior session genuinely has no
    previous close, and defaulting to the current one would render as a
    confident 0.00% rather than "unknown"."""
    if len(frame) < 2:
        return None
    value = frame["Close"].iloc[-2]
    return None if pd.isna(value) else float(value)


def upsert_signal(db, ticker: str, sig_row: pd.Series, prev_close: Optional[float] = None) -> None:
    def clean(v):
        return None if pd.isna(v) else float(v)

    as_of = sig_row.name.date() if hasattr(sig_row.name, "date") else sig_row.name
    values = dict(
        as_of_date=as_of,
        close=clean(sig_row["Close"]),
        prev_close=prev_close,
        donchian_high=clean(sig_row["DonchianHigh_N"]),
        donchian_low=clean(sig_row["DonchianLow_M"]),
        atr=clean(sig_row["ATR"]),
        stop_distance=clean(sig_row["StopDistance"]),
        entry_signal=bool(sig_row["EntrySignal"]),
        exit_signal=bool(sig_row["ExitSignal"]),
        suggested_shares=clean(sig_row["Shares"]),
        updated_at=utcnow(),
    )

    existing = db.query(Signal).filter(Signal.ticker == ticker).first()
    if existing:
        for k, v in values.items():
            setattr(existing, k, v)
    else:
        db.add(Signal(ticker=ticker, **values))


def upsert_rsi_signal(db, ticker: str, sig_row: pd.Series, prev_close: Optional[float] = None) -> None:
    """Same upsert, for the RSI(2) pullback strategy. Deliberately a
    near-copy of `upsert_signal` rather than a shared generic writer: the
    two tables hold different indicator columns, and the abstraction that
    unified them would have to be a column map, which is harder to read
    than the two explicit dicts."""
    def clean(v):
        return None if pd.isna(v) else float(v)

    as_of = sig_row.name.date() if hasattr(sig_row.name, "date") else sig_row.name
    values = dict(
        as_of_date=as_of,
        close=clean(sig_row["Close"]),
        prev_close=prev_close,
        rsi=clean(sig_row["RSI"]),
        sma_fast=clean(sig_row["SMA_Fast"]),
        sma_trend=clean(sig_row["SMA_Trend"]),
        atr=clean(sig_row["ATR"]),
        stop_distance=clean(sig_row["StopDistance"]),
        entry_signal=bool(sig_row["EntrySignal"]),
        exit_signal=bool(sig_row["ExitSignal"]),
        suggested_shares=clean(sig_row["Shares"]),
        updated_at=utcnow(),
    )

    existing = db.query(RsiSignal).filter(RsiSignal.ticker == ticker).first()
    if existing:
        for k, v in values.items():
            setattr(existing, k, v)
    else:
        db.add(RsiSignal(ticker=ticker, **values))


def run_pipeline(fetch_fn: Callable[[List[str]], Dict[str, pd.DataFrame]] = fetch_ohlc_batch) -> None:
    if DEMO_MODE:
        print(
            "[pipeline] Refusing to run with DEMO_MODE=true — this pipeline writes real "
            "computed signals from real market data, which shouldn't land in the fake demo "
            "database. Unset DEMO_MODE, or use `python -m app.seed_demo` for fake data."
        )
        sys.exit(1)

    configure_yfinance_cache()
    Base.metadata.create_all(bind=engine)
    sync_new_columns()
    db = SessionLocal()
    try:
        equities = get_sp500_tickers() + get_extra_equity_tickers()
        commodities = get_commodity_tickers()
        index_etfs = get_index_etf_tickers()
        held = [t for (t,) in db.query(Holding.ticker).all()]

        universe = sorted(set(equities) | set(commodities) | set(index_etfs))
        # Union with holdings: a held ticker outside the default universe
        # (e.g. dropped out of the S&P 500) still needs its exit signal
        # computed — that's a hard requirement from the brief.
        all_tickers = sorted(set(universe) | set(held))

        # The two strategies scan different universes. Donchian breakouts
        # run over everything; RSI(2) is restricted to baskets, because
        # mean reversion on single companies was measured and did not work
        # (backend/README.md). Bars are fetched once for the union, so the
        # narrower scan costs no extra requests.
        rsi_tickers = set(rsi_scan_universe(held))

        print(
            f"[pipeline] {len(equities)} equities + {len(commodities)} commodities = "
            f"{len(universe)} in scan universe; {len(held)} held; "
            f"{len(all_tickers)} tickers to fetch."
        )

        db.query(Universe).delete()
        # Later groups don't overwrite earlier ones — `ticker` is the
        # primary key, so a duplicate would raise rather than reclassify.
        seen: set[str] = set()
        for group, asset_class in (
            (index_etfs, "etf"),
            (commodities, "commodity"),
            (equities, "equity"),
        ):
            for t in group:
                if t in seen:
                    continue
                seen.add(t)
                db.add(Universe(ticker=t, asset_class=asset_class, active=True))
        db.commit()

        computed, failed = 0, 0
        rsi_computed, rsi_failed = 0, 0
        for i in range(0, len(all_tickers), CHUNK_SIZE):
            chunk = all_tickers[i:i + CHUNK_SIZE]
            print(f"[pipeline] Fetching {i + 1}-{i + len(chunk)} of {len(all_tickers)}...")

            try:
                data = fetch_fn(chunk)
            except Exception as e:
                print(f"[pipeline] WARNING: batch fetch failed for this chunk ({e}); skipping.")
                failed += len(chunk)
                continue

            for ticker in chunk:
                df = data.get(ticker)
                if df is None or df.empty:
                    failed += 1
                    continue
                prev = previous_close(df)
                try:
                    sig = compute_signal_frame(df)
                    last = sig.iloc[-1]
                    if pd.isna(last["Close"]):
                        failed += 1
                        continue
                    upsert_signal(db, ticker, last, prev)
                    computed += 1
                except Exception as e:
                    print(f"[pipeline] WARNING: signal calc failed for {ticker} ({e})")
                    failed += 1

                # Second strategy, same bars — no extra fetch. Caught
                # separately so a fault in the newer strategy can never
                # cost the Donchian signals, which are what the exit
                # alerts on the main page depend on.
                if ticker in rsi_tickers:
                    try:
                        rsi_last = compute_rsi_signal_frame(df).iloc[-1]
                        if not pd.isna(rsi_last["Close"]):
                            upsert_rsi_signal(db, ticker, rsi_last, prev)
                            rsi_computed += 1
                    except Exception as e:
                        print(f"[pipeline] WARNING: RSI calc failed for {ticker} ({e})")
                        rsi_failed += 1

            db.commit()  # commit progress per chunk rather than one giant transaction
            if i + CHUNK_SIZE < len(all_tickers):
                time.sleep(PAUSE_BETWEEN_CHUNKS_SEC)  # be polite to Yahoo's rate limits

        # Position sizing depends on account equity, and equity depends on
        # what your holdings are worth — which isn't known until the loop
        # above has fetched their closes. So it's resolved here, at the end,
        # and the stored column is brought in line in one pass.
        #
        # The API recomputes this on every read anyway (see app/account.py),
        # so these stored values are a snapshot rather than the source of
        # truth. Writing them keeps the column honest for anyone querying
        # the database directly instead of through the API.
        # Drop RSI rows for anything no longer in that strategy's scope —
        # a ticker sold, or the whole S&P 500 after the universe narrowed
        # to baskets. Upserts alone would leave those rows behind, and a
        # stale row is worse than a missing one: it would keep offering
        # last month's signal as today's recommendation.
        pruned = (
            db.query(RsiSignal)
            .filter(RsiSignal.ticker.notin_(rsi_tickers))
            .delete(synchronize_session=False)
        )
        if pruned:
            print(f"[pipeline] Pruned {pruned} RSI signal(s) outside the basket universe.")
        db.commit()

        equity, source = resolve_equity(db)
        settings = get_settings(db)
        restated = 0
        # Both strategies size off the same equity and the same risk
        # fraction — only their stop distances differ — so the restatement
        # covers both tables.
        for sig in [*db.query(Signal).all(), *db.query(RsiSignal).all()]:
            shares = suggested_shares(sig.stop_distance, equity, settings.risk_fraction)
            if shares is not None:
                sig.suggested_shares = shares
                restated += 1
        db.commit()

        print(
            f"[pipeline] Account equity ${equity:,.0f} ({source}), "
            f"risking {settings.risk_fraction:.1%} = ${settings.risk_fraction * equity:,.0f} "
            f"per position; restated sizing on {restated} tickers."
        )
        print(
            f"[pipeline] Done. Donchian: {computed} updated, {failed} skipped/failed. "
            f"RSI(2): {rsi_computed} updated, {rsi_failed} failed."
        )
    finally:
        db.close()


if __name__ == "__main__":
    run_pipeline()
