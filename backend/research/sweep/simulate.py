"""The simulation.

Pure: takes strategy contexts and a config, returns a result. No network,
no database, no filesystem.

`app/backtest/engine.py` could not be used for this and was deliberately
left alone. It fills at a bar's open on a market order, is long-only, and
has one row per day; this strategy rests a limit order inside a gap, holds
a stop and a target simultaneously, goes both ways, and lives on 5-minute
bars. Bending the daily engine to cover all four would have put the
dashboard's measured Donchian numbers at risk to answer a research
question.

## The pessimistic conventions, stated once

Each of these is a place where a backtest can quietly pay itself. All four
are resolved AGAINST the strategy:

1. **A limit order fills only on a trade THROUGH it**, not on a touch.
   `low < limit` for a buy, not `low <= limit`. A touch means the price
   printed; it does not mean a resting order at the back of the queue got
   filled.
2. **Same-bar stop and target resolve to the stop.** When a bar's range
   covers both, daily-or-coarser data cannot say which came first, and the
   strategy's own setups — a stop just beyond a sweep wick — are exactly
   the shape that makes the flattering answer wrong.
3. **A position is checked for its stop on its own entry bar.** The daily
   engine skips this, but there a fill is an opening print and here it is
   a retracement into a gap mid-bar, with the rest of the bar still to
   come.
4. **Gaps fill at the open, not at the order price.** A bar that opens
   through the stop fills there — worse. A bar that opens through the
   target also fills at the open, which is better, and that asymmetry is
   real rather than generous: both are the same rule, "a resting order
   fills at the first available price".

## Sharpe is computed on DAILY equity

`app/backtest/metrics.py` annualises with sqrt(252), which assumes one
equity point per trading day. Handing it a 5-minute curve would multiply
the volatility figure by roughly sqrt(78) and divide Sharpe by the same —
a number that looks like a result and is an artefact of the bar size. So
the simulator keeps the full-resolution curve for plotting and hands
`summarize` a daily one. This is the single most dangerous thing about
reusing daily-built metrics on intraday data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Sequence

import numpy as np
import pandas as pd

from .bars import trading_day
from .bias import Direction
from .instruments import Instrument, resolve
from .params import SweepParams
from .strategy import Setup, StrategyContext

MergeMode = Literal["sweep_only", "parallel", "combined", "donchian_veto"]


@dataclass(frozen=True)
class SimConfig:
    initial_equity: float = 100_000.0
    risk_fraction: float = 0.01

    #: Per-symbol contract specs — tick size, point value and friction.
    #: Anything not listed falls back to `instruments.resolve`, which
    #: means the equity model. See that module for why this is not a
    #: detail: charging notional basis points to a futures contract
    #: misprices friction by two orders of magnitude.
    instruments: Dict[str, Instrument] = field(default_factory=dict)

    max_positions: int = 20
    max_position_fraction: float = 0.20
    #: Gross exposure cap as a multiple of equity. 1.0 is the no-margin
    #: book the dashboard runs; 2.0 was measured for the shorting
    #: experiments and made things worse, so it stays opt-in.
    max_gross: float = 1.0
    min_notional: float = 100.0
    allow_fractional: bool = True

    allow_long: bool = True
    allow_short: bool = True

    #: See convention 3 above.
    check_stop_on_entry_bar: bool = True

    day_boundary_hour: int = 18

    def instrument(self, symbol: str) -> Instrument:
        return self.instruments.get(symbol) or resolve(symbol)


@dataclass(frozen=True)
class SimTrade:
    """One round trip.

    Field names match `app/backtest/engine.Trade` wherever they mean the
    same thing, because `metrics.summarize` reads them positionally by
    attribute: `pnl`, `pnl_pct`, `r_multiple`, `bars_held`. Renaming any
    of those four silently empties the trade statistics block rather than
    raising.
    """

    symbol: str
    direction: Direction
    entry_ts: pd.Timestamp
    entry_price: float
    shares: float
    exit_ts: pd.Timestamp
    exit_price: float
    exit_reason: str
    entry_cost: float
    exit_cost: float
    borrow_cost: float
    risk_dollars: float
    stop_distance: float
    bars_held: int
    reward_risk: float
    #: Dollars per 1.0 of price movement per unit. 1.0 for shares, 50 for
    #: an ES contract. Carried on the trade rather than looked up at read
    #: time so a saved trades file stays interpretable on its own.
    point_value: float = 1.0
    #: Account equity when this position was opened, for reporting what
    #: fraction of the account each trade actually risked.
    equity_at_entry: float = 0.0
    tag: str = "sweep"

    @property
    def sign(self) -> int:
        return 1 if self.direction == "bullish" else -1

    @property
    def exposure(self) -> float:
        return self.shares * self.entry_price * self.point_value

    @property
    def gross_pnl(self) -> float:
        return self.sign * self.shares * self.point_value * (self.exit_price - self.entry_price)

    @property
    def pnl(self) -> float:
        return self.gross_pnl - self.entry_cost - self.exit_cost - self.borrow_cost

    @property
    def pnl_pct(self) -> float:
        return self.pnl / self.exposure if self.exposure else 0.0

    @property
    def risk_fraction_realised(self) -> float:
        """What fraction of the account this trade actually put at risk.

        The target is `SimConfig.risk_fraction`, but on a 5-minute
        strategy the stop is small enough that risk-based sizing asks for
        many times the account, so the exposure caps bind instead and the
        realised figure lands far below the target. Reported because a
        run whose trades each risk 0.01% is not measuring the strategy at
        the size anyone would trade it.
        """
        return self.risk_dollars / self.equity_at_entry if self.equity_at_entry else 0.0

    @property
    def r_multiple(self) -> float:
        return self.pnl / self.risk_dollars if self.risk_dollars else 0.0


@dataclass
class OpenPosition:
    symbol: str
    direction: Direction
    shares: float
    entry_price: float
    entry_ts: pd.Timestamp
    entry_bar: int
    entry_cost: float
    risk_dollars: float
    stop: Optional[float]
    target: Optional[float]
    reward_risk: float
    last_price: float
    equity_at_entry: float = 0.0
    borrow_accrued: float = 0.0
    tag: str = "sweep"

    @property
    def sign(self) -> int:
        return 1 if self.direction == "bullish" else -1


@dataclass(frozen=True)
class ExternalOrder:
    """A trade proposed by another system, funded from the same account.

    This is how `merge_mode="combined"` works: the Donchian engine's own
    trade list is replayed as orders here, so the two strategies compete
    for cash and slots. The replay is faithful because Donchian's exit
    (a close below the 20-day low) is a property of prices, not of
    capital — so its exit date does not change when the account is
    shared. Its ENTRY may well be skipped for lack of funds, and that
    crowding is exactly what the mode exists to measure.
    """

    symbol: str
    direction: Direction
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    stop_distance: float
    tag: str = "donchian"


@dataclass
class SimResult:
    equity: pd.Series
    daily_equity: pd.Series
    cash: pd.Series
    gross_exposure: pd.Series
    positions_count: pd.Series
    trades: List[SimTrade]
    config: SimConfig
    params: Optional[SweepParams] = None
    summary: dict = field(default_factory=dict)
    funnel: Dict[str, int] = field(default_factory=dict)
    #: Why setups never became trades.
    order_outcomes: Dict[str, int] = field(default_factory=dict)

    def trades_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "tag": t.tag,
                    "entry_ts": t.entry_ts,
                    "entry_price": t.entry_price,
                    "exit_ts": t.exit_ts,
                    "exit_price": t.exit_price,
                    "exit_reason": t.exit_reason,
                    "shares": t.shares,
                    "pnl": t.pnl,
                    "pnl_pct": t.pnl_pct,
                    "r_multiple": t.r_multiple,
                    "bars_held": t.bars_held,
                    "reward_risk": t.reward_risk,
                }
                for t in self.trades
            ]
        )


def _bar_lookup(clock: pd.DatetimeIndex, index: pd.DatetimeIndex) -> np.ndarray:
    """For each clock instant, this symbol's row there, or -1.

    Built once per symbol instead of searching per bar. -1 rather than NaN
    so the consumer must branch on it explicitly: a symbol with no print
    at an instant is not tradeable there, and silently reusing its last
    bar would invent liquidity.
    """
    positions = np.searchsorted(index.values, clock.values)
    out = np.full(len(clock), -1, dtype=np.int64)
    valid = positions < len(index)
    matched = np.zeros(len(clock), dtype=bool)
    matched[valid] = index.values[positions[valid]] == clock.values[valid]
    out[matched] = positions[matched]
    return out


def run_simulation(
    contexts: Sequence[StrategyContext],
    config: SimConfig,
    external: Optional[Sequence[ExternalOrder]] = None,
) -> SimResult:
    """Walk the clock once, resolving exits before entries."""
    if not contexts and not external:
        raise ValueError("Nothing to simulate — no contexts and no external orders.")

    frames = {c.symbol: c.timeframes.execution for c in contexts}
    # Seeded from a real frame rather than from an empty DatetimeIndex.
    # An empty index is tz-NAIVE, and unioning it with a tz-aware one does
    # not raise — it returns an object-dtype index, and every later
    # comparison fails somewhere far from the cause. `prices.py` carries
    # the same warning for the same reason.
    clock: Optional[pd.DatetimeIndex] = None
    for frame in frames.values():
        clock = frame.index if clock is None else clock.union(frame.index)
    if clock is None or not len(clock):
        raise ValueError("No bars to simulate.")

    rows = {symbol: _bar_lookup(clock, frame.index) for symbol, frame in frames.items()}
    arrays = {
        symbol: {
            "open": frame["Open"].to_numpy(dtype=float),
            "high": frame["High"].to_numpy(dtype=float),
            "low": frame["Low"].to_numpy(dtype=float),
            "close": frame["Close"].to_numpy(dtype=float),
        }
        for symbol, frame in frames.items()
    }

    pending = _pending_by_bar(contexts, clock, config)
    external_entries, external_exits = _external_by_bar(external or (), clock)

    cash = config.initial_equity
    positions: Dict[str, OpenPosition] = {}
    resting: List[tuple] = []  # (symbol, Setup)
    trades: List[SimTrade] = []
    outcomes: Dict[str, int] = {}

    equity_curve = np.empty(len(clock), dtype=float)
    cash_curve = np.empty(len(clock), dtype=float)
    gross_curve = np.zeros(len(clock), dtype=float)
    count_curve = np.zeros(len(clock), dtype=float)
    equity_curve[0] = cash_curve[0] = config.initial_equity

    def note(outcome: str) -> None:
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    def price_of(symbol: str, i: int, field: str) -> float:
        row = rows[symbol][i]
        return arrays[symbol][field][row] if row >= 0 else float("nan")

    def mark(symbol: str, i: int) -> Optional[float]:
        value = price_of(symbol, i, "close")
        return float(value) if np.isfinite(value) else None

    def equity_now(i: int) -> float:
        total = cash
        for pos in positions.values():
            last = mark(pos.symbol, i)
            if last is not None:
                pos.last_price = last
            # A short's proceeds are already sitting in cash, so its mark
            # enters with a negative sign as the liability to buy it back.
            # Equity immediately after a short is therefore equity before
            # it less costs, which is what makes the two directions
            # comparable without a separate short ledger.
            total += pos.sign * config.instrument(pos.symbol).exposure(pos.shares, pos.last_price)
        return total

    def close_position(
        pos: OpenPosition, price: float, i: int, reason: str
    ) -> None:
        nonlocal cash
        spec = config.instrument(pos.symbol)
        exposure = spec.exposure(pos.shares, price)
        exit_cost = spec.cost(pos.shares, price)
        # A long sells (cash in); a short buys back (cash out). The
        # proceeds of the short went into cash at entry, so this is the
        # symmetric leg of that same movement.
        cash += pos.sign * exposure - exit_cost
        trades.append(
            SimTrade(
                symbol=pos.symbol,
                direction=pos.direction,
                entry_ts=pos.entry_ts,
                entry_price=pos.entry_price,
                shares=pos.shares,
                exit_ts=clock[i],
                exit_price=price,
                exit_reason=reason,
                entry_cost=pos.entry_cost,
                exit_cost=exit_cost,
                borrow_cost=pos.borrow_accrued,
                risk_dollars=pos.risk_dollars,
                stop_distance=abs(pos.entry_price - pos.stop) if pos.stop is not None else 0.0,
                bars_held=i - pos.entry_bar,
                reward_risk=pos.reward_risk,
                point_value=spec.point_value,
                equity_at_entry=pos.equity_at_entry,
                tag=pos.tag,
            )
        )

    def resolve_exit(pos: OpenPosition, i: int) -> Optional[tuple]:
        """(price, reason) if this bar closes the position, else None."""
        row = rows[pos.symbol][i]
        if row < 0:
            return None
        bar = arrays[pos.symbol]
        open_, high, low = bar["open"][row], bar["high"][row], bar["low"][row]
        if not np.isfinite(high) or not np.isfinite(low):
            return None

        if pos.direction == "bullish":
            hit_stop = pos.stop is not None and low <= pos.stop
            hit_target = pos.target is not None and high >= pos.target
            if hit_stop:  # convention 2: the stop wins ties
                fill = open_ if np.isfinite(open_) and open_ < pos.stop else pos.stop
                return float(fill), "stop"
            if hit_target:
                fill = open_ if np.isfinite(open_) and open_ > pos.target else pos.target
                return float(fill), "target"
            return None

        hit_stop = pos.stop is not None and high >= pos.stop
        hit_target = pos.target is not None and low <= pos.target
        if hit_stop:
            fill = open_ if np.isfinite(open_) and open_ > pos.stop else pos.stop
            return float(fill), "stop"
        if hit_target:
            fill = open_ if np.isfinite(open_) and open_ < pos.target else pos.target
            return float(fill), "target"
        return None

    def gross_of(i: int) -> float:
        total = 0.0
        for pos in positions.values():
            last = mark(pos.symbol, i)
            price = last if last is not None else pos.last_price
            total += config.instrument(pos.symbol).exposure(pos.shares, price)
        return total

    def try_open(
        symbol: str,
        direction: Direction,
        price: float,
        stop: Optional[float],
        target: Optional[float],
        risk_per_unit: float,
        i: int,
        equity: float,
        reward_risk: float,
        tag: str,
    ) -> bool:
        nonlocal cash
        if direction == "bullish" and not config.allow_long:
            note("long_disallowed")
            return False
        if direction == "bearish" and not config.allow_short:
            note("short_disallowed")
            return False
        if len(positions) >= config.max_positions:
            note("no_free_slot")
            return False
        if not np.isfinite(price) or price <= 0 or risk_per_unit <= 0:
            note("unpriceable")
            return False

        spec = config.instrument(symbol)
        # Every cap is expressed in EXPOSURE (price x point value), not in
        # units, so a futures contract is bounded by the $247k it controls
        # rather than by the 4,950 it is quoted at.
        per_unit_exposure = spec.exposure(1.0, price)
        target_shares = (config.risk_fraction * equity) / (risk_per_unit * spec.point_value)
        by_fraction = config.max_position_fraction * equity / per_unit_exposure
        headroom = config.max_gross * equity - gross_of(i)
        by_gross = headroom / per_unit_exposure if headroom > 0 else 0.0
        shares = min(target_shares, by_fraction, by_gross)
        if not config.allow_fractional:
            shares = float(np.floor(shares))
        if not np.isfinite(shares) or shares <= 0 or spec.exposure(shares, price) < config.min_notional:
            note("no_capital" if by_gross < by_fraction else "not_sizable")
            return False

        exposure = spec.exposure(shares, price)
        entry_cost = spec.cost(shares, price)
        cash += -exposure if direction == "bullish" else exposure
        cash -= entry_cost
        positions[symbol] = OpenPosition(
            symbol=symbol,
            direction=direction,
            shares=shares,
            entry_price=price,
            entry_ts=clock[i],
            entry_bar=i,
            entry_cost=entry_cost,
            # What one stop distance actually costs this position, derived
            # from the size rather than from the budget — the two differ
            # whenever a cap bound the size, and recording the budget
            # would make those trades' R multiples fiction.
            risk_dollars=spec.risk_dollars(shares, risk_per_unit),
            equity_at_entry=equity,
            stop=stop,
            target=target,
            reward_risk=reward_risk,
            last_price=price,
            tag=tag,
        )
        note("filled")
        return True

    previous_ts = clock[0]
    for i in range(len(clock)):
        now = clock[i]
        elapsed_days = max((now - previous_ts).total_seconds(), 0.0) / 86_400.0
        previous_ts = now

        # --- 1. Borrow on shorts ----------------------------------------
        # Calendar time, not bars: a short pays to be borrowed over the
        # weekend just as it does on a Tuesday.
        if elapsed_days:
            for pos in positions.values():
                spec = config.instrument(pos.symbol)
                if pos.direction == "bearish" and spec.borrow_annual:
                    fee = spec.exposure(pos.shares, pos.last_price) * spec.borrow_annual * elapsed_days
                    pos.borrow_accrued += fee
                    cash -= fee

        # --- 2. Exits, before entries so the cash is reusable ------------
        for symbol in list(positions):
            pos = positions[symbol]
            outcome = resolve_exit(pos, i)
            if outcome is None:
                continue
            price, reason = outcome
            close_position(pos, price, i, reason)
            del positions[symbol]

        equity = equity_now(i) if i else config.initial_equity

        # --- 3. Expire resting orders ------------------------------------
        still_resting = []
        for symbol, setup in resting:
            if now > setup.expires_at:
                note("expired_unfilled")
                continue
            still_resting.append((symbol, setup))
        resting = still_resting

        # --- 4. Admit setups that became valid on or before this bar -----
        for symbol, setup in pending.get(i, ()):
            resting.append((symbol, setup))

        # --- 5. Limit fills -----------------------------------------------
        # Sorted by reward:risk so that when capital is short, the setups
        # with the most asymmetric payoff are funded first — the same
        # principle as the daily engine's Rank, applied to this strategy's
        # own stated source of expectancy.
        resting.sort(key=lambda item: -item[1].reward_risk)
        unfilled = []
        for symbol, setup in resting:
            if symbol in positions:
                # One position per instrument. A second setup on the same
                # symbol is a different read of the same move, not a
                # second independent bet.
                note("already_in_position")
                continue
            row = rows[symbol][i]
            if row < 0:
                unfilled.append((symbol, setup))
                continue
            bar = arrays[symbol]
            open_, high, low = bar["open"][row], bar["high"][row], bar["low"][row]
            filled = False
            if setup.direction == "bullish" and np.isfinite(low) and low < setup.entry_limit:
                price = min(setup.entry_limit, open_) if np.isfinite(open_) else setup.entry_limit
                filled = try_open(
                    symbol, "bullish", float(price), setup.stop, setup.target,
                    abs(float(price) - setup.stop), i, equity, setup.reward_risk, "sweep",
                )
            elif setup.direction == "bearish" and np.isfinite(high) and high > setup.entry_limit:
                price = max(setup.entry_limit, open_) if np.isfinite(open_) else setup.entry_limit
                filled = try_open(
                    symbol, "bearish", float(price), setup.stop, setup.target,
                    abs(setup.stop - float(price)), i, equity, setup.reward_risk, "sweep",
                )
            if not filled:
                unfilled.append((symbol, setup))
        resting = unfilled

        # --- 6. External orders (merge_mode="combined") -------------------
        for order in external_entries.get(i, ()):
            if order.symbol in positions:
                note("external_already_held")
                continue
            price = price_of(order.symbol, i, "open")
            if not np.isfinite(price):
                note("external_no_price")
                continue
            try_open(
                order.symbol, order.direction, float(price), None, None,
                order.stop_distance, i, equity, float("nan"), order.tag,
            )

        # --- 7. Forced exits for external orders --------------------------
        for symbol in list(positions):
            pos = positions[symbol]
            if pos.tag == "sweep" or pos.entry_bar == i:
                continue
            if symbol in external_exits.get(i, set()):
                price = price_of(symbol, i, "open")
                if np.isfinite(price):
                    close_position(pos, float(price), i, "external_exit")
                    del positions[symbol]

        # --- 7b. Stops on the entry bar (convention 3) --------------------
        # Step 2 runs before entries, so without this pass a position
        # opened on bar `i` would first be checked on `i+1` — and the
        # convention would be documented but not implemented. The check
        # uses the whole bar's range, including the part that traded
        # before the fill, which is the pessimistic reading: intraday
        # order is unknowable from an OHLC bar, and the alternative pays
        # the strategy for the ambiguity.
        if config.check_stop_on_entry_bar:
            for symbol in list(positions):
                pos = positions[symbol]
                if pos.entry_bar != i:
                    continue
                outcome = resolve_exit(pos, i)
                if outcome is None:
                    continue
                price, reason = outcome
                close_position(pos, price, i, f"{reason}_same_bar")
                del positions[symbol]

        # --- 8. Mark to market ---------------------------------------------
        equity_curve[i] = equity_now(i)
        cash_curve[i] = cash
        gross_curve[i] = gross_of(i)
        count_curve[i] = len(positions)

    # Force-close the book, or everything still underwater at the end
    # vanishes from the win rate.
    last = len(clock) - 1
    for symbol in list(positions):
        pos = positions[symbol]
        price = mark(symbol, last)
        close_position(pos, price if price is not None else pos.last_price, last, "end_of_run")
        del positions[symbol]
    equity_curve[last] = equity_now(last)

    equity = pd.Series(equity_curve, index=clock, name="equity")
    daily = _daily_equity(equity, config.day_boundary_hour)

    from app.backtest import metrics  # local: keeps this module import-light

    result = SimResult(
        equity=equity,
        daily_equity=daily,
        cash=pd.Series(cash_curve, index=clock, name="cash"),
        gross_exposure=pd.Series(gross_curve / np.maximum(equity_curve, 1e-9), index=clock),
        positions_count=pd.Series(count_curve, index=clock, name="positions"),
        trades=trades,
        config=config,
        params=contexts[0].params if contexts else None,
        order_outcomes=outcomes,
    )
    result.funnel = _merge_funnels(contexts)
    # Daily curve, daily-calibrated annualisation — see the module docstring.
    daily_counts = _daily_reduce(result.positions_count, config.day_boundary_hour, "max")
    daily_gross = _daily_reduce(result.gross_exposure, config.day_boundary_hour, "mean")
    result.summary = metrics.summarize(daily, trades, daily_counts, daily_gross)
    # Two figures metrics.py has no reason to know about, both of which
    # decide whether the headline return means anything.
    #
    # `avg_risk_fraction` is the sizing reality check: the config asks for
    # `risk_fraction` of equity per trade, and on a strategy whose stop is
    # a few ticks wide the exposure caps bind long before that is reached.
    # A run whose trades each risk 0.01% of the account has measured the
    # rules but not the strategy, and its return is not comparable to a
    # daily system's.
    realised = [t.risk_fraction_realised for t in trades if t.equity_at_entry]
    result.summary["avg_risk_fraction"] = float(np.mean(realised)) if realised else None
    result.summary["target_risk_fraction"] = config.risk_fraction
    result.summary["gross_costs"] = float(
        sum(t.entry_cost + t.exit_cost + t.borrow_cost for t in trades)
    )
    result.summary["n_long"] = sum(1 for t in trades if t.direction == "bullish")
    result.summary["n_short"] = sum(1 for t in trades if t.direction == "bearish")
    return result


def _daily_equity(equity: pd.Series, day_boundary_hour: int) -> pd.Series:
    """One point per trading day: the last mark of each session.

    Indexed by the trading day's own date, so a Sunday-evening session
    open belongs to Monday — the same rule `bars.trading_day` applies
    everywhere else.
    """
    days = [trading_day(ts, day_boundary_hour) for ts in equity.index]
    frame = pd.DataFrame({"equity": equity.to_numpy(), "day": days})
    grouped = frame.groupby("day")["equity"].last()
    grouped.index = pd.DatetimeIndex(grouped.index)
    grouped.name = "equity"
    return grouped


def _daily_reduce(series: pd.Series, day_boundary_hour: int, how: str) -> pd.Series:
    """Collapse an intraday series to one value per trading day.

    Grouped by `bars.trading_day` rather than by `resample("1D")` for two
    reasons: a calendar day would split an evening session from the
    New York session it belongs to, and a tz-aware resample returns a
    tz-aware index that reindexes against the naive daily equity index as
    all-NaN — which reads as "never in the market" instead of as an error.
    """
    days = [trading_day(ts, day_boundary_hour) for ts in series.index]
    frame = pd.DataFrame({"value": series.to_numpy(dtype=float), "day": days})
    grouped = frame.groupby("day")["value"].agg(how)
    grouped.index = pd.DatetimeIndex(grouped.index)
    return grouped


def _pending_by_bar(
    contexts: Sequence[StrategyContext], clock: pd.DatetimeIndex, config: SimConfig
) -> Dict[int, List[tuple]]:
    """Setups bucketed by the clock position they first become valid at.

    `side="left"` on the search is the look-ahead gate: a setup whose
    `valid_from` is a bar's close is admitted on the NEXT clock instant,
    never on the bar that produced it.
    """
    out: Dict[int, List[tuple]] = {}
    values = clock.values
    for context in contexts:
        for setup in context.setups:
            position = int(np.searchsorted(values, setup.valid_from.to_datetime64(), side="left"))
            if position >= len(clock):
                continue
            out.setdefault(position, []).append((context.symbol, setup))
    return out


def _external_by_bar(orders: Sequence[ExternalOrder], clock: pd.DatetimeIndex):
    """Entries and exits bucketed by clock position, as two separate maps.

    Two maps rather than one keyed by sign: an off-by-one in a sign trick
    would silently exit the wrong bar, and that is not a failure any test
    here would obviously catch.
    """
    entries: Dict[int, List[ExternalOrder]] = {}
    exits: Dict[int, set] = {}
    values = clock.values
    for order in orders:
        at = int(np.searchsorted(values, order.entry_ts.to_datetime64(), side="left"))
        if at < len(clock):
            entries.setdefault(at, []).append(order)
        out_at = int(np.searchsorted(values, order.exit_ts.to_datetime64(), side="left"))
        if out_at < len(clock):
            exits.setdefault(out_at, set()).add(order.symbol)
    return entries, exits


def _merge_funnels(contexts: Sequence[StrategyContext]) -> Dict[str, int]:
    total: Dict[str, int] = {}
    for context in contexts:
        for key, value in context.funnel().items():
            total[key] = total.get(key, 0) + value
    return total
