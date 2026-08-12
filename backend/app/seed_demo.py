"""
Seeds data/demo.db (or data/real.db if you point it there — not recommended)
with fake tickers, fake OHLC history, and computed signals, run through the
exact same compute_signal_frame() the real pipeline will use.

Run with DEMO_MODE=true so it writes to demo.db, matching what the API
will read when DEMO_MODE is on:

    DEMO_MODE=true python -m app.seed_demo

This is a stand-in for the daily pipeline script (not yet built). Once that
exists, it replaces this for the real.db path; this seeder stays around
permanently to keep the public demo populated.
"""

from datetime import datetime

import numpy as np
import pandas as pd

from .account import get_settings, resolve_equity, suggested_shares
from .database import Base, SessionLocal, engine, sync_new_columns
from .models import Holding, Signal, Universe
from .signals import compute_signal_frame
from .timeutil import utcnow

# Deliberately fictitious names/tickers — never real market tickers —
# so a public demo can't be mistaken for real symbols or advice.
DEMO_TICKERS = [
    # (ticker, asset_class, regime) — regime forces a rally/decline into
    # the final sessions so the demo actually has both entry and exit
    # signals to show, instead of an all-flat channel.
    ("ACME", "equity", "down"),       # held + exit signal demo
    ("GLOBEX", "equity", "flat"),     # held, no signal
    ("INITECH", "equity", "up"),      # entry candidate
    ("UMBRELLA", "equity", "flat"),
    ("STARKIND", "equity", "up"),     # entry candidate
    ("WAYNEIND", "equity", "flat"),
    ("HOOLI", "equity", "flat"),
    ("VANDELAY", "equity", "up"),     # entry candidate
    ("DEMOGOLD", "commodity", "flat"),  # held, no signal
    ("DEMOSLVR", "commodity", "down"),
]

DEMO_HOLDINGS = ["ACME", "GLOBEX", "DEMOGOLD"]


def synthetic_ohlc(days=300, start_price=100.0, seed=None, regime="flat"):
    """Random-walk OHLC. `regime` optionally forces an accelerating
    rally ("up") or decline ("down") into the final ~12 sessions so
    today's close actually breaks the rolling channel — otherwise
    entry/exit signals almost never fire in a plain random walk."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=datetime.today(), periods=days)
    drift, vol = 0.0002, 0.015
    rets = rng.normal(drift, vol, size=days)

    if regime in ("up", "down"):
        tail = 12
        ramp = np.linspace(0.006, 0.022, tail)
        rets[-tail:] += ramp if regime == "up" else -ramp

    close = start_price * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0.004, 0.003, size=days)))
    low = close * (1 - np.abs(rng.normal(0.004, 0.003, size=days)))
    open_ = low + (high - low) * rng.random(days)
    volume = rng.integers(1_000_000, 5_000_000, size=days)

    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


def seed():
    Base.metadata.create_all(bind=engine)
    sync_new_columns()
    db = SessionLocal()
    try:
        db.query(Signal).delete()
        db.query(Universe).delete()
        db.query(Holding).delete()

        rng = np.random.default_rng(7)

        for i, (ticker, asset_class, regime) in enumerate(DEMO_TICKERS):
            db.add(Universe(ticker=ticker, asset_class=asset_class, active=True))

            df = synthetic_ohlc(seed=42 + i, start_price=50 + i * 15, regime=regime)
            sig = compute_signal_frame(df)
            last = sig.iloc[-1]

            def clean(v):
                return None if pd.isna(v) else float(v)

            db.add(
                Signal(
                    ticker=ticker,
                    as_of_date=sig.index[-1].date(),
                    close=clean(last["Close"]),
                    prev_close=clean(sig["Close"].iloc[-2]) if len(sig) >= 2 else None,
                    donchian_high=clean(last["DonchianHigh_N"]),
                    donchian_low=clean(last["DonchianLow_M"]),
                    atr=clean(last["ATR"]),
                    stop_distance=clean(last["StopDistance"]),
                    entry_signal=bool(last["EntrySignal"]),
                    exit_signal=bool(last["ExitSignal"]),
                    suggested_shares=clean(last["Shares"]),
                    updated_at=utcnow(),
                )
            )

            if ticker in DEMO_HOLDINGS:
                db.add(
                    Holding(
                        ticker=ticker,
                        shares=round(float(rng.uniform(10, 200)), 2),
                    )
                )

        db.commit()

        # Give the demo a cash balance so equity resolves via the "derived"
        # path (holdings + cash) rather than falling back to the default —
        # the public demo should show the real sizing behaviour, not the
        # placeholder one.
        settings = get_settings(db)
        settings.cash_balance = 25_000.0
        settings.risk_fraction = 0.01
        settings.equity_override = None
        db.commit()

        # Restate sizing now that holdings and closes both exist, matching
        # what the pipeline does at the end of a real run.
        equity, source = resolve_equity(db)
        for sig in db.query(Signal).all():
            shares = suggested_shares(sig.stop_distance, equity, settings.risk_fraction)
            if shares is not None:
                sig.suggested_shares = shares
        db.commit()

        print(
            f"Seeded {len(DEMO_TICKERS)} tickers, {len(DEMO_HOLDINGS)} demo holdings. "
            f"Account equity ${equity:,.0f} ({source})."
        )
    finally:
        db.close()

    seed_backtest()


def seed_backtest() -> None:
    """Runs the real backtest engine over synthetic history so the demo's
    backtest panel is populated.

    Deliberately the real engine on fake data rather than fake results:
    the demo then demonstrates the actual code path, and there's no
    separate mock to drift out of sync with the thing it's imitating.
    """
    from datetime import date, timedelta

    from .backtest.benchmark import buy_and_hold
    from .backtest.config import BacktestConfig
    from .backtest.engine import build_calendar, run_backtest
    from .backtest.store import save_result

    frames = {
        ticker: synthetic_ohlc(days=760, seed=900 + i, start_price=40 + i * 12, regime=regime)
        for i, (ticker, _, regime) in enumerate(DEMO_TICKERS)
    }
    benchmark = synthetic_ohlc(days=760, seed=7, start_price=100.0, regime="up")

    first = min(f.index[0] for f in frames.values()).date()
    last = max(f.index[-1] for f in frames.values()).date()

    for use_stop in (False, True):
        config = BacktestConfig(
            start=first - timedelta(days=1),
            end=last + timedelta(days=1),
            tickers=tuple(sorted(frames)),
            benchmark="DEMOBENCH",
            use_atr_stop=use_stop,
            label=f"demo {'with ATR stop' if use_stop else 'faithful'}",
        )
        result = run_backtest(frames, config, benchmark)

        calendar = build_calendar(frames, config, benchmark)
        bench_equity = buy_and_hold(benchmark, calendar, config)
        if bench_equity is not None:
            from .backtest import metrics

            result.benchmark_equity = bench_equity
            result.benchmark_summary = metrics.summarize(bench_equity, [])

        save_result(result, universe_label="demo")

    print("Seeded demo backtest (both variants).")


if __name__ == "__main__":
    seed()
