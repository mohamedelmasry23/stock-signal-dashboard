"""Donchian longs and one short rule, in one account.

The long side is `app.backtest.engine.run_backtest`, reproduced step for
step: same decision/execution split, same stale sweep, same exits-before-
entries order, same sizing and caps, same membership gate, same costs.
With the short rule switched off the equity curve must match
`run_backtest` to the cent — `test_longs_alone_reproduce_run_backtest`
pins that on synthetic data, and `evaluate.py` re-checks it on the real
universe before printing anything. That check is the only reason a number
from this file can sit next to the dashboard's +164%.

`app/backtest/engine.py` itself is untouched. It is long-only by design,
and the measured Donchian numbers must not move to answer a research
question.

## Shorts

* **Entry** at the execution bar's open, on a signal from the decision
  bar. Cancelled, not carried, if that open is already through the stop
  or the target — the setup no longer exists.
* **Sizing**: `risk_fraction` of equity over the ACTUAL stop distance at
  decision time (stop minus the decision close), capped like a long.
* **Collateral**: a short reserves its full notional from cash, as a long
  spends it. No margin — the same no-leverage book as experiments 1-4.
  Marked as `shares * (2 * entry - price)`: the collateral plus the P&L.
* **Borrow**: `borrow_annual` of the short's notional, per trading day.
* **Exits** are resting orders checked intrabar, on the entry bar too
  (the fill was the open, so the whole bar is after it). A bar reaching
  both stop and target resolves to the STOP. A bar opening through either
  fills at the open — worse for a stop, better for a target, both real.
* **Trailing** stops (S1) move down to `ShortTrail` on each execution bar,
  never up.

## Priority

Each day, long candidates are funded before short candidates. The shorts
exist to complement the Donchian book, and earlier experiments measured
what happens when they compete with it on equal terms (long profit fell
from $163k to as little as $26k). Positions already open still share the
20 slots and the cash, so crowding is measured rather than assumed away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from app.account import suggested_shares
from app.backtest.config import BacktestConfig
from app.backtest.engine import build_calendar
from app.backtest.membership import MembershipSchedule, align
from app.backtest.metrics import TRADING_DAYS, summarize
from app.signals import compute_signal_frame

from research.sweep.simulate import SimTrade


@dataclass(frozen=True)
class ShortConfig:
    rule: Optional[Callable] = None
    params: object = None
    enable_longs: bool = True
    borrow_annual: float = 0.005


@dataclass
class _Pos:
    ticker: str
    direction: str
    shares: float
    entry_price: float
    entry_index: int
    entry_cost: float
    risk_dollars: float
    stop_distance: float
    equity_at_entry: float
    last_price: float
    stop: float = float("nan")
    target: float = float("nan")
    trails: bool = False
    stale_bars: int = 0
    borrow: float = 0.0

    def value(self, price: float) -> float:
        if self.direction == "bullish":
            return self.shares * price
        return self.shares * (2.0 * self.entry_price - price)


@dataclass
class LongShortResult:
    equity: pd.Series
    trades: List[SimTrade]
    positions_count: pd.Series
    exposure: pd.Series
    summary: dict = field(default_factory=dict)
    skipped_for_capital: int = 0
    short_outcomes: Dict[str, int] = field(default_factory=dict)

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "ticker": t.symbol, "direction": t.direction, "entry_ts": t.entry_ts,
            "entry_price": t.entry_price, "exit_ts": t.exit_ts, "exit_price": t.exit_price,
            "exit_reason": t.exit_reason, "shares": t.shares, "pnl": t.pnl,
            "pnl_pct": t.pnl_pct, "r_multiple": t.r_multiple, "bars_held": t.bars_held,
        } for t in self.trades])


def _arrays(frame: pd.DataFrame, calendar: pd.DatetimeIndex, config: BacktestConfig,
            shorts: ShortConfig) -> dict:
    signals = compute_signal_frame(
        frame, n_entry=config.n_entry, m_exit=config.m_exit,
        atr_period=config.atr_period, k_atr=config.k_atr,
    )
    # Identical to the dashboard's /api/entry-candidates ranking and to
    # `_donchian_frame` in app/backtest/strategies.py.
    signals["Rank"] = (signals["Close"] - signals["DonchianHigh_N"]) / signals["ATR"]
    if shorts.rule is not None:
        short = shorts.rule(signals, shorts.params) if shorts.params is not None else shorts.rule(signals)
        signals = signals.join(short)
    # Rules run on the ticker's own bars, THEN align to the calendar with
    # no fill — the same order `_aligned` uses, for the same reason.
    signals = signals.reindex(calendar)
    out = {k.lower(): signals[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close", "ATR")}
    out["stop_distance"] = signals["StopDistance"].to_numpy(dtype=float)
    out["rank"] = signals["Rank"].to_numpy(dtype=float)
    out["entry"] = signals["EntrySignal"].fillna(False).to_numpy(dtype=bool)
    out["exit"] = signals["ExitSignal"].fillna(False).to_numpy(dtype=bool)
    if shorts.rule is not None:
        out["s_entry"] = signals["ShortEntry"].fillna(False).to_numpy(dtype=bool)
        for k in ("ShortStop", "ShortTarget", "ShortRank", "ShortTrail"):
            out[k] = signals[k].to_numpy(dtype=float)
    return out


def run_long_short(
    prices: Dict[str, pd.DataFrame],
    config: BacktestConfig,
    shorts: ShortConfig = ShortConfig(),
    benchmark_frame: Optional[pd.DataFrame] = None,
    membership: Optional[MembershipSchedule] = None,
) -> LongShortResult:
    if config.index_overlay or config.use_atr_stop:
        raise ValueError("overlay and ATR-stop variants are not reproduced here")
    if config.sizing_mode != "risk" or config.fill != "next_open":
        raise ValueError("only risk sizing at the next open is reproduced here")

    tickers = sorted(t for t in prices if len(prices[t]) > 0)
    calendar = build_calendar(prices, config, benchmark_frame)
    data = {t: _arrays(prices[t], calendar, config, shorts) for t in tickers}
    index = align(membership, calendar, tickers) if membership is not None else {}

    cash = config.initial_equity
    positions: Dict[str, _Pos] = {}
    trades: List[SimTrade] = []
    skipped = 0
    outcomes: Dict[str, int] = {}

    n = len(calendar)
    equity_curve = np.empty(n); equity_curve[0] = config.initial_equity
    exposure_curve = np.zeros(n); count_curve = np.zeros(n)
    cost_rate = config.total_cost_rate
    cash_daily = ((1 + config.cash_yield_annual) ** (1 / TRADING_DAYS) - 1) if config.cash_yield_annual else 0.0
    borrow_daily = shorts.borrow_annual / TRADING_DAYS

    def note(key: str) -> None:
        outcomes[key] = outcomes.get(key, 0) + 1

    def close(pos: _Pos, price: float, bar: int, reason: str) -> None:
        nonlocal cash
        exit_cost = pos.shares * price * cost_rate
        cash += pos.value(price) - exit_cost
        trades.append(SimTrade(
            symbol=pos.ticker, direction=pos.direction,
            entry_ts=calendar[pos.entry_index], entry_price=pos.entry_price,
            shares=pos.shares, exit_ts=calendar[bar], exit_price=price,
            exit_reason=reason, entry_cost=pos.entry_cost, exit_cost=exit_cost,
            borrow_cost=pos.borrow, risk_dollars=pos.risk_dollars,
            stop_distance=pos.stop_distance, bars_held=bar - pos.entry_index,
            reward_risk=float("nan"), equity_at_entry=pos.equity_at_entry,
            tag="donchian" if pos.direction == "bullish" else "short",
        ))

    def short_exit(pos: _Pos, x: int) -> Optional[tuple]:
        arr = data[pos.ticker]
        if pos.trails:
            trail = arr["ShortTrail"][x]
            if np.isfinite(trail):
                pos.stop = min(pos.stop, trail)
        high, low, open_ = arr["high"][x], arr["low"][x], arr["open"][x]
        if not (np.isfinite(high) and np.isfinite(low)):
            return None
        if high >= pos.stop:                      # the stop wins ties
            return (open_ if np.isfinite(open_) and open_ > pos.stop else pos.stop), "stop"
        if np.isfinite(pos.target) and low <= pos.target:
            return (open_ if np.isfinite(open_) and open_ < pos.target else pos.target), "target"
        return None

    for i in range(1, n):
        d, x = i - 1, i
        equity_for_sizing = equity_curve[d]
        risk_dollars = config.risk_fraction * equity_for_sizing

        # 1. stale sweep — both sides
        for t in list(positions):
            pos = positions[t]
            if np.isfinite(data[t]["close"][x]):
                pos.stale_bars = 0
            else:
                pos.stale_bars += 1
                if pos.stale_bars > config.max_stale_bars:
                    close(pos, pos.last_price, x, "stale")
                    del positions[t]

        # 2. long exits: the Donchian breakdown, at the open
        for t in list(positions):
            pos = positions[t]
            if pos.direction != "bullish" or not data[t]["exit"][d]:
                continue
            price = data[t]["open"][x]
            if not np.isfinite(price):
                continue
            close(pos, price, x, "donchian_exit")
            del positions[t]

        # 3. short exits on positions opened before this bar
        for t in list(positions):
            pos = positions[t]
            if pos.direction != "bearish":
                continue
            hit = short_exit(pos, x)
            if hit is not None:
                close(pos, float(hit[0]), x, hit[1])
                del positions[t]

        # 4. entries — longs first, then shorts
        candidates = []
        for t in tickers:
            if t in positions:
                continue
            row = index.get(t)
            if row is not None and not row.member[d]:
                continue
            arr = data[t]
            if shorts.enable_longs and arr["entry"][d]:
                sd, rk = arr["stop_distance"][d], arr["rank"][d]
                if np.isfinite(sd) and sd > 0 and np.isfinite(rk):
                    candidates.append((0, -rk, t, sd))
            if shorts.rule is not None and arr["s_entry"][d]:
                rk = arr["ShortRank"][d]
                candidates.append((1, -(rk if np.isfinite(rk) else 0.0), t, float("nan")))
        candidates.sort()

        for side, _, t, stop_distance in candidates:
            if t in positions:
                continue  # a long and a short signal on one ticker: the long got it
            arr = data[t]
            if len(positions) >= config.max_positions:
                skipped += 1
                if side:
                    note("no_free_slot")
                continue
            raw = arr["open"][x]
            if not np.isfinite(raw) or raw <= 0:
                if side:
                    note("no_fill_price")
                continue
            fill_price = raw * (1 + cost_rate)
            by_cash = cash / fill_price

            if side == 0:
                target = suggested_shares(float(stop_distance), equity_for_sizing, config.risk_fraction)
                if target is None or not np.isfinite(target) or target <= 0:
                    continue
                by_fraction = config.max_position_fraction * equity_for_sizing / fill_price
                shares = min(target, by_fraction, by_cash)
                if not np.isfinite(shares) or shares <= 0:
                    continue
                if not config.allow_fractional:
                    shares = float(np.floor(shares))
                if shares <= 0 or shares * fill_price < config.min_notional:
                    if target > by_cash:
                        skipped += 1
                    continue
                if shares < target:
                    skipped += 1
                notional = shares * raw
                entry_cost = notional * cost_rate
                cash -= notional + entry_cost
                positions[t] = _Pos(
                    t, "bullish", shares, raw, x, entry_cost, risk_dollars,
                    float(stop_distance), equity_for_sizing, raw,
                )
                continue

            # --- a short ---
            stop = arr["ShortStop"][d]
            tgt = arr["ShortTarget"][d]
            decided_at = arr["close"][d]
            if not (np.isfinite(stop) and np.isfinite(decided_at) and stop > decided_at):
                note("no_valid_stop")
                continue
            if raw >= stop:
                note("opened_through_stop")
                continue
            if np.isfinite(tgt) and raw <= tgt:
                note("opened_through_target")
                continue
            per_share = stop - decided_at
            target_sh = risk_dollars / per_share
            by_fraction = config.max_position_fraction * equity_for_sizing / fill_price
            shares = min(target_sh, by_fraction, by_cash)
            if not config.allow_fractional:
                shares = float(np.floor(shares))
            if not np.isfinite(shares) or shares <= 0 or shares * fill_price < config.min_notional:
                note("no_cash" if by_cash < by_fraction else "not_sizable")
                if target_sh > by_cash:
                    skipped += 1
                continue
            if shares < target_sh:
                skipped += 1
            notional = shares * raw
            entry_cost = notional * cost_rate
            cash -= notional + entry_cost
            pos = _Pos(
                t, "bearish", shares, raw, x, entry_cost,
                # What one stop distance actually costs from the real fill.
                shares * (stop - raw), stop - raw, equity_for_sizing, raw,
                stop=float(stop), target=float(tgt),
                # Always on: `short_exit` skips any bar where the rule has no
                # trail value. Deciding it from the ENTRY bar's value turned
                # trailing off for the whole trade whenever that one bar was
                # missing — caught by test_a_trailing_stop_only_moves_down.
                trails=True,
            )
            positions[t] = pos
            note("filled")
            # The fill was this bar's open, so the rest of the bar came after
            # it and the orders are live immediately.
            hit = short_exit(pos, x)
            if hit is not None:
                close(pos, float(hit[0]), x, hit[1] + "_same_bar")
                del positions[t]

        # 5. interest on idle cash, borrow on shorts
        if cash_daily and cash > 0:
            cash *= 1.0 + cash_daily
        for pos in positions.values():
            if pos.direction == "bearish" and borrow_daily:
                price = data[pos.ticker]["close"][x]
                fee = pos.shares * (price if np.isfinite(price) else pos.last_price) * borrow_daily
                cash -= fee
                pos.borrow += fee

        # 6. mark to market
        held = gross = 0.0
        for pos in positions.values():
            price = data[pos.ticker]["close"][x]
            if np.isfinite(price):
                pos.last_price = price
            held += pos.value(pos.last_price)
            gross += pos.shares * pos.last_price
        equity_curve[x] = cash + held
        count_curve[x] = len(positions)
        exposure_curve[x] = gross / equity_curve[x] if equity_curve[x] > 0 else 0.0

    last = n - 1
    for t in list(positions):
        pos = positions[t]
        price = data[t]["close"][last]
        close(pos, price if np.isfinite(price) else pos.last_price, last, "end_of_backtest")
        del positions[t]

    equity = pd.Series(equity_curve, index=calendar, name="equity")
    result = LongShortResult(
        equity=equity, trades=trades,
        positions_count=pd.Series(count_curve, index=calendar),
        exposure=pd.Series(exposure_curve, index=calendar),
        skipped_for_capital=skipped, short_outcomes=outcomes,
    )
    result.summary = summarize(equity, trades, result.positions_count, result.exposure, config.risk_free_annual)
    return result
