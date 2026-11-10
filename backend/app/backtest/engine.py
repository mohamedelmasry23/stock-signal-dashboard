"""
The simulation.

Pure: takes `{ticker: OHLC DataFrame}` and a config, returns a result. No
network, no database, no filesystem — which is what lets the whole test
suite run offline and deterministically.

## The one rule

Every bar involves two rows:

    decision bar  d = calendar[i-1]   everything the strategy may KNOW
    execution bar x = calendar[i]     fill price and mark-to-market only

A signal computed from bar `d`'s close was not knowable until after that
close, so it cannot be traded at that close. Splitting the two roles is
the whole defence against look-ahead, and `test_truncation_invariance`
exists to prove nothing leaks across it.

Two consequences worth stating because both are easy to get backwards:

* `compute_signal_frame` already `.shift(1)`s the Donchian levels. The
  engine must NOT shift them again — the one-bar delay lives entirely in
  the d/x split. Double-shifting is the classic off-by-one here.

* `ATR` and `StopDistance` are NOT
  shifted; they include their own bar. That is fine and is not look-ahead,
  so long as they are read from `d` and never from `x`.

The asymmetry between those two points is deliberate and is the thing most
likely to be "fixed" by mistake later: only the Donchian *levels* shift,
because a breakout level containing today's own high could never be
broken. `test_truncation_invariance` covers both strategies and is what
would catch a well-meaning edit that made them consistent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..account import suggested_shares
from .config import BacktestConfig
from .membership import MembershipSchedule, additions_without_prices, align
from .metrics import TRADING_DAYS
from .strategies import get_strategy


@dataclass(frozen=True)
class Trade:
    ticker: str
    entry_date: date
    entry_price: float
    shares: float
    exit_date: date
    exit_price: float
    exit_reason: str
    entry_cost: float
    exit_cost: float
    # Both captured as of the DECISION bar, so a trade record can be
    # checked against what the strategy actually knew when it sized.
    risk_dollars: float
    stop_distance: float
    bars_held: int
    # "signal" for the strategy's own entry; "index_addition" for a buy on
    # an S&P addition announcement (the index-change overlay).
    entry_reason: str = "signal"

    @property
    def gross_pnl(self) -> float:
        return self.shares * (self.exit_price - self.entry_price)

    @property
    def pnl(self) -> float:
        return self.gross_pnl - self.entry_cost - self.exit_cost

    @property
    def pnl_pct(self) -> float:
        notional = self.shares * self.entry_price
        return self.pnl / notional if notional else 0.0

    @property
    def r_multiple(self) -> float:
        """P&L as a multiple of the risk the position was sized for.

        The interesting number is how often this goes below -1 on losers:
        the position was sized so one stop distance costs `risk_fraction`
        of equity, but with no stop order behind it, nothing enforces that.
        """
        return self.pnl / self.risk_dollars if self.risk_dollars else 0.0


@dataclass
class Position:
    ticker: str
    shares: float
    entry_price: float
    entry_index: int
    entry_cost: float
    risk_dollars: float
    stop_distance: float
    stop_price: Optional[float]
    last_price: float
    stale_bars: int = 0
    entry_reason: str = "signal"


@dataclass
class BacktestResult:
    equity: pd.Series
    cash: pd.Series
    exposure: pd.Series
    positions_count: pd.Series
    trades: List[Trade]
    config: BacktestConfig
    benchmark_equity: Optional[pd.Series] = None
    summary: dict = field(default_factory=dict)
    benchmark_summary: dict = field(default_factory=dict)
    skipped_for_capital: int = 0
    # What happened to each addition the overlay tried to buy, by outcome.
    # Empty unless config.index_overlay.
    overlay: dict = field(default_factory=dict)


def _aligned(frame: pd.DataFrame, calendar: pd.DatetimeIndex, config: BacktestConfig) -> dict:
    """Signals computed on the ticker's OWN index, then reindexed onto the
    master calendar with no fill.

    The ordering matters: forward-filling prices before computing signals
    would manufacture flat bars, which compress ATR toward zero and freeze
    the Donchian channel — inventing both a tighter stop and a stale
    breakout level. A ticker simply isn't tradeable on a date it has no
    bar for.

    *Which* signals get computed is the strategy's business, not this
    function's — see app/backtest/strategies.py. Everything downstream
    consumes only entry/exit/atr/stop_distance/rank, so a strategy that
    supplies those needs no change here.
    """
    signals = get_strategy(config.strategy).frame(frame, config).reindex(calendar)

    return {
        "open": signals["Open"].to_numpy(dtype=float),
        "high": signals["High"].to_numpy(dtype=float),
        "low": signals["Low"].to_numpy(dtype=float),
        "close": signals["Close"].to_numpy(dtype=float),
        "atr": signals["ATR"].to_numpy(dtype=float),
        "stop_distance": signals["StopDistance"].to_numpy(dtype=float),
        # Candidate quality, for the days when capital can't fund every
        # signal. Non-finite disqualifies — which is how a missing close,
        # a missing level or a zero ATR are all rejected in one test.
        "rank": signals["Rank"].to_numpy(dtype=float),
        # fillna(False) because a NaN bar must not read as a signal.
        "entry": signals["EntrySignal"].fillna(False).to_numpy(dtype=bool),
        "exit": signals["ExitSignal"].fillna(False).to_numpy(dtype=bool),
    }


def build_calendar(
    prices: Dict[str, pd.DataFrame],
    config: BacktestConfig,
    benchmark_frame: Optional[pd.DataFrame] = None,
) -> pd.DatetimeIndex:
    """Master trading calendar, clipped to the configured window.

    Prefers the benchmark's index when available: it's a liquid US equity
    session calendar, so it excludes days when only a futures market was
    open and no equity could actually be traded.
    """
    if benchmark_frame is not None and len(benchmark_frame):
        index = benchmark_frame.index
    else:
        index = pd.DatetimeIndex([])
        for frame in prices.values():
            index = index.union(frame.index)

    index = pd.DatetimeIndex(index).sort_values()
    start = pd.Timestamp(config.start)
    end = pd.Timestamp(config.end)
    return index[(index >= start) & (index <= end)]


def run_backtest(
    prices: Dict[str, pd.DataFrame],
    config: BacktestConfig,
    benchmark_frame: Optional[pd.DataFrame] = None,
    membership: Optional[MembershipSchedule] = None,
) -> BacktestResult:
    """`membership`, when given, restricts entries to tickers that are
    index members on the decision bar; tickers outside the schedule are
    unrestricted. `config.index_overlay` additionally trades the
    announcements themselves. With neither, the run is unchanged."""
    from . import metrics  # local import: metrics imports Trade for typing

    if config.index_overlay and membership is None:
        raise ValueError("index_overlay needs a membership schedule to trade")

    tickers = sorted(t for t in prices if len(prices[t]) > 0)
    calendar = build_calendar(prices, config, benchmark_frame)
    if len(calendar) < 2:
        raise ValueError(
            f"Calendar has {len(calendar)} bars in [{config.start}, {config.end}] — "
            "nothing to simulate. Widen the window or check the price data."
        )

    data = {t: _aligned(prices[t], calendar, config) for t in tickers}
    index = align(membership, calendar, tickers) if membership is not None else {}
    overlay = config.index_overlay

    cash = config.initial_equity
    positions: Dict[str, Position] = {}
    trades: List[Trade] = []
    skipped_for_capital = 0
    # Outcome of every addition announcement the overlay acted on. A buy
    # can fail for reasons worth reporting separately — above all a
    # spin-off that has no price history yet on its announcement day.
    overlay_outcomes: Dict[str, int] = {}
    if overlay:
        unpriced = additions_without_prices(membership, calendar, tickers)
        if unpriced:
            overlay_outcomes["no_price_data"] = len(unpriced)
    # A removal is announced on one bar but can only be sold on a bar with
    # a print; carried until then (or until the stale sweep takes it).
    removal_pending: set = set()

    def note(outcome: str) -> None:
        overlay_outcomes[outcome] = overlay_outcomes.get(outcome, 0) + 1

    equity_curve = np.empty(len(calendar), dtype=float)
    cash_curve = np.empty(len(calendar), dtype=float)
    exposure_curve = np.zeros(len(calendar), dtype=float)
    count_curve = np.zeros(len(calendar), dtype=float)

    equity_curve[0] = cash_curve[0] = config.initial_equity
    cost_rate = config.total_cost_rate
    fill_field = "open" if config.fill == "next_open" else "close"
    # Compounded on trading days only, matching how the annualising in
    # metrics.py treats a year (252 bars, not 365 days).
    cash_daily_rate = (
        (1.0 + config.cash_yield_annual) ** (1.0 / TRADING_DAYS) - 1.0
        if config.cash_yield_annual
        else 0.0
    )

    def close_position(pos: Position, price: float, bar: int, reason: str) -> None:
        nonlocal cash
        proceeds = pos.shares * price
        exit_cost = proceeds * cost_rate
        cash += proceeds - exit_cost
        trades.append(
            Trade(
                ticker=pos.ticker,
                entry_date=calendar[pos.entry_index].date(),
                entry_price=pos.entry_price,
                shares=pos.shares,
                exit_date=calendar[bar].date(),
                exit_price=price,
                exit_reason=reason,
                entry_cost=pos.entry_cost,
                exit_cost=exit_cost,
                risk_dollars=pos.risk_dollars,
                stop_distance=pos.stop_distance,
                bars_held=bar - pos.entry_index,
                entry_reason=pos.entry_reason,
            )
        )

    for i in range(1, len(calendar)):
        d, x = i - 1, i

        # --- 0. Snapshot -------------------------------------------------
        # Sizing uses the equity marked at the DECISION bar's close: you
        # sized last night, before this bar opened. Using the execution
        # bar's equity would inflate size on exactly the gap-up mornings
        # breakouts tend to fill on.
        equity_for_sizing = equity_curve[d]
        risk_dollars = config.risk_fraction * equity_for_sizing

        # --- 1. Stale sweep ---------------------------------------------
        # A ticker that stops printing bars (delisted, halted, acquired)
        # would otherwise be carried at a frozen mark to the end of the run
        # and booked as a flat trade. Force it out at its last known price.
        for ticker in list(positions):
            pos = positions[ticker]
            if np.isfinite(data[ticker]["close"][x]):
                pos.stale_bars = 0
            else:
                pos.stale_bars += 1
                if pos.stale_bars > config.max_stale_bars:
                    close_position(pos, pos.last_price, x, "stale")
                    del positions[ticker]

        # --- 1b. Index removals (overlay only) --------------------------
        # Announced after d's close, sold at x's open. Ahead of the
        # strategy's own exits so a holding that is both being removed and
        # breaking down is recorded as the removal it primarily is.
        if overlay:
            for ticker in positions:
                if ticker in index and index[ticker].removed[d]:
                    removal_pending.add(ticker)
            removal_pending &= positions.keys()
            for ticker in sorted(removal_pending):
                price = data[ticker][fill_field][x]
                if not np.isfinite(price):
                    continue  # no print on x; retry next bar
                close_position(positions[ticker], price, x, "index_removal")
                del positions[ticker]
                removal_pending.discard(ticker)

        # --- 2. Donchian exits ------------------------------------------
        # Signalled by d's close, filled on x. Runs before entries so the
        # proceeds are available to fund this bar's entries — i.e. same-day
        # settlement, which is stated in the report.
        for ticker in list(positions):
            if not data[ticker]["exit"][d]:
                continue
            price = data[ticker][fill_field][x]
            if not np.isfinite(price):
                continue  # no tradeable print; carry and retry next bar
            # Costs are charged inside close_position as a deduction from
            # proceeds rather than as a worse fill price — same effect on
            # P&L, and it keeps the recorded exit_price a real market print.
            close_position(positions[ticker], price, x, "donchian_exit")
            del positions[ticker]

        # --- 3. ATR stop (comparison variant only) ----------------------
        # Modelled as a resting stop order: it triggers intrabar when the
        # low pierces it, filling at the stop price — or at the open if the
        # bar gapped straight through, which is where the protection
        # actually fails. Positions opened on this same bar are not checked
        # until the next one.
        if config.use_atr_stop:
            for ticker in list(positions):
                pos = positions[ticker]
                if pos.stop_price is None or pos.entry_index == x:
                    continue
                low, open_ = data[ticker]["low"][x], data[ticker]["open"][x]
                if not np.isfinite(low) or low > pos.stop_price:
                    continue
                fill = open_ if np.isfinite(open_) and open_ < pos.stop_price else pos.stop_price
                close_position(pos, fill, x, "atr_stop")
                del positions[ticker]

        # --- 4. Entries --------------------------------------------------
        candidates = []
        for ticker in tickers:
            membership_row = index.get(ticker)
            # Announced as an addition after d's close: the overlay buys it
            # at x's open whether or not the strategy signalled.
            news = overlay and membership_row is not None and bool(membership_row.added[d])
            if ticker in positions:
                if news:
                    note("already_held")
                continue
            if membership_row is not None and not membership_row.member[d]:
                continue  # not an index member when the decision was made
            arr = data[ticker]
            if not (arr["entry"][d] or news):
                continue
            stop_distance = arr["stop_distance"][d]
            # np.isfinite, not `is None`: suggested_shares() returns NaN
            # for a NaN stop distance (NaN <= 0 is False), and one NaN
            # share count turns cash and every later equity point into NaN.
            if not (np.isfinite(stop_distance) and stop_distance > 0):
                if news:
                    # Usually a spin-off: no bars yet, so no ATR to size by.
                    note("no_price_history")
                continue
            # The strategy's own ranking key, computed by the same formula
            # the matching /api/…entry-candidates endpoint uses. When a day
            # produces more signals than max_positions can hold, this
            # ordering, not the entry rule, decides what actually gets traded.
            rank = arr["rank"][d]
            if not np.isfinite(rank):
                if not news:
                    continue
                rank = 0.0
            # News entries sort ahead of every ordinary signal: the
            # announcement gets first claim on a free slot. Without an
            # overlay the first element is constant, so the order — and
            # every earlier result — is unchanged. Ticker as tiebreak so a
            # run is reproducible rather than dependent on dict ordering.
            candidates.append((0 if news else 1, -rank, ticker, stop_distance))

        candidates.sort()

        for priority, _, ticker, stop_distance in candidates:
            news = priority == 0
            if len(positions) >= config.max_positions:
                skipped_for_capital += 1
                if news:
                    note("no_free_slot")
                continue

            raw_price = data[ticker][fill_field][x]
            if not np.isfinite(raw_price) or raw_price <= 0:
                # Signalled but untradeable on x (halt, missing bar).
                # Cancel rather than carry: carrying would let the engine
                # pick its entry bar with hindsight.
                if news:
                    note("no_fill_price")
                continue

            fill_price = raw_price * (1 + cost_rate)
            by_cash = cash / fill_price

            if config.sizing_mode == "equal_weight":
                # A flat slice of the account per position. No
                # max_position_fraction here: the slice already bounds the
                # position, and applying the cap as well would re-impose
                # the throttle this mode exists to remove.
                target = (equity_for_sizing / config.target_positions) / fill_price
                shares = min(target, by_cash)
            else:
                target = suggested_shares(
                    float(stop_distance), equity_for_sizing, config.risk_fraction
                )
                if target is None or not np.isfinite(target) or target <= 0:
                    if news:
                        note("not_sizable")
                    continue
                # Caps the live app doesn't have. Risk-based sizing on a
                # low-volatility name routinely asks for multiples of the
                # account; cash alone would still allow one position to be
                # the entire portfolio.
                by_fraction = config.max_position_fraction * equity_for_sizing / fill_price
                shares = min(target, by_fraction, by_cash)

            if not np.isfinite(shares) or shares <= 0:
                if news:
                    # target and by_fraction are positive here, so a zero
                    # means by_cash was: the account was fully spent.
                    note("no_cash" if by_cash <= 0 else "not_sizable")
                continue

            if not config.allow_fractional:
                shares = float(np.floor(shares))
            if shares <= 0 or shares * fill_price < config.min_notional:
                if target > by_cash:
                    skipped_for_capital += 1
                if news:
                    # Cash, not sizing, is what usually stops a news buy:
                    # a fully invested trend book has little left over.
                    note("no_cash" if target > by_cash else "not_sizable")
                continue
            if shares < target:
                skipped_for_capital += 1
            if news:
                note("bought" if shares >= target else "bought_smaller_than_intended")

            notional = shares * raw_price
            entry_cost = notional * cost_rate
            cash -= notional + entry_cost
            positions[ticker] = Position(
                ticker=ticker,
                shares=shares,
                entry_price=raw_price,
                entry_index=x,
                entry_cost=entry_cost,
                # What one stop_distance of adverse move actually costs.
                #
                # In `risk` mode that is the intended budget by
                # construction, so this is the same figure. In
                # equal_weight it is NOT — position size came from the
                # allocation, not from a risk target — so recording the
                # intended budget would make every R-multiple fiction.
                # Deriving it from the position keeps "losses worse than
                # 1R" meaning the same thing in both modes.
                risk_dollars=(
                    shares * float(stop_distance)
                    if config.sizing_mode == "equal_weight"
                    else risk_dollars
                ),
                stop_distance=float(stop_distance),
                # stop_distance is ALREADY ATR * k_atr (see signals.py), so
                # the stop sits one stop_distance below entry. Multiplying
                # by k_atr again would place it at k_atr^2 * ATR — a 4x-ATR
                # stop masquerading as a 2x one.
                stop_price=raw_price - float(stop_distance) if config.use_atr_stop else None,
                last_price=raw_price,
                entry_reason="index_addition" if news else "signal",
            )

        # --- 5. Interest on idle cash ------------------------------------
        # Accrued after this bar's trading, so a position opened today
        # doesn't also earn a day of interest on the money that bought it.
        # Zero by default, which leaves every previously recorded result
        # bit-for-bit unchanged.
        if cash_daily_rate and cash > 0:
            cash *= 1.0 + cash_daily_rate

        # --- 6. Mark to market -------------------------------------------
        holdings_value = 0.0
        for pos in positions.values():
            price = data[pos.ticker]["close"][x]
            if np.isfinite(price):
                pos.last_price = price
            holdings_value += pos.shares * pos.last_price

        equity_curve[x] = cash + holdings_value
        cash_curve[x] = cash
        count_curve[x] = len(positions)
        exposure_curve[x] = holdings_value / equity_curve[x] if equity_curve[x] > 0 else 0.0

    # Force-close whatever is still open, otherwise every position still
    # underwater at the end silently vanishes from the win rate.
    last = len(calendar) - 1
    for ticker in list(positions):
        pos = positions[ticker]
        price = data[ticker]["close"][last]
        close_position(pos, price if np.isfinite(price) else pos.last_price, last, "end_of_backtest")
        del positions[ticker]

    equity = pd.Series(equity_curve, index=calendar, name="equity")
    result = BacktestResult(
        equity=equity,
        cash=pd.Series(cash_curve, index=calendar, name="cash"),
        exposure=pd.Series(exposure_curve, index=calendar, name="exposure"),
        positions_count=pd.Series(count_curve, index=calendar, name="positions"),
        trades=trades,
        config=config,
        skipped_for_capital=skipped_for_capital,
        overlay=overlay_outcomes,
    )
    result.summary = metrics.summarize(
        equity, trades, result.positions_count, result.exposure, config.risk_free_annual
    )
    return result
