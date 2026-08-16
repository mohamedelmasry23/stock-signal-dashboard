"""Backtest parameters. Pure data — no I/O, no imports from the engine."""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional

from ..signals import RSI_ENTRY_THRESHOLD, RSI_EXIT_THRESHOLD

# Strategy defaults deliberately mirror app/signals.py's, so an unmodified
# run measures the system the dashboard actually implements. Every one is
# overridable from the CLI for sensitivity work.
#
# The RSI thresholds are imported rather than restated for a reason beyond
# tidiness: they are Connors' published values, not values fitted to this
# data, which is exactly what makes a run of them out-of-sample. A second
# copy here would eventually drift from the strategy's own and quietly
# turn a published parameter into a fitted one.
DEFAULT_N_ENTRY = 55
DEFAULT_M_EXIT = 20
DEFAULT_ATR_PERIOD = 20
DEFAULT_K_ATR = 2.0

DEFAULT_RSI_PERIOD = 2
DEFAULT_RSI_TREND_PERIOD = 200
DEFAULT_RSI_EXIT_MA_PERIOD = 5

StrategyName = Literal["donchian", "rsi"]
SizingMode = Literal["risk", "equal_weight"]
FillTiming = Literal["next_open", "next_close"]
PriceMode = Literal["adjusted", "raw"]

# Equal-weight slots. Four means each position targets a quarter of equity,
# so four concurrent signals deploy the account fully.
DEFAULT_TARGET_POSITIONS = 4


@dataclass(frozen=True)
class BacktestConfig:
    start: date
    end: date

    tickers: tuple[str, ...] = ()
    benchmark: str = "SPY"

    initial_equity: float = 100_000.0
    risk_fraction: float = 0.01

    # Which system is being measured. See app/backtest/strategies.py.
    strategy: StrategyName = "donchian"

    # Donchian windows — passed straight through to compute_signal_frame.
    n_entry: int = DEFAULT_N_ENTRY
    m_exit: int = DEFAULT_M_EXIT

    # Shared by both strategies: sizing divides into StopDistance, so these
    # two are what make a position risked identically whichever strategy
    # proposed it.
    atr_period: int = DEFAULT_ATR_PERIOD
    k_atr: float = DEFAULT_K_ATR

    # RSI(2) pullback parameters. Ignored when strategy == "donchian".
    rsi_period: int = DEFAULT_RSI_PERIOD
    rsi_entry_threshold: float = RSI_ENTRY_THRESHOLD
    rsi_exit_threshold: float = RSI_EXIT_THRESHOLD
    rsi_trend_period: int = DEFAULT_RSI_TREND_PERIOD
    rsi_exit_ma_period: int = DEFAULT_RSI_EXIT_MA_PERIOD

    # The dashboard has no stop-loss: its only exit is the Donchian
    # breakdown. False measures that system faithfully; True adds a hard
    # k_atr stop so the two can be compared. See `variant`.
    use_atr_stop: bool = False

    # Round-trip friction, applied on entry AND exit. Split out so the
    # report can say which part is commission-like and which is slippage.
    cost_bps: float = 5.0
    slippage_bps: float = 5.0

    # A signal is known only after its bar closes, so fills happen on the
    # NEXT bar. next_open is the realistic default.
    fill: FillTiming = "next_open"

    # How much to buy, which is a property of the strategy rather than of
    # the engine:
    #
    #   risk          shares = risk_fraction x equity / stop_distance.
    #                 Trend following's model — size inversely to
    #                 volatility so every position risks the same amount.
    #
    #   equal_weight  each position targets equity / target_positions.
    #                 What short-term mean reversion is actually traded
    #                 with, and the difference is not cosmetic: under
    #                 `risk` with a 20% cap, the RSI strategy averaged 1.84
    #                 concurrent positions and left roughly three quarters
    #                 of the account idle, which scaled its return down by
    #                 several times before the strategy was even judged.
    #
    # Sharpe is invariant to this choice — scaling every position scales
    # return and volatility together — so switching modes changes the
    # headline return, not the quality of the edge.
    sizing_mode: SizingMode = "risk"
    target_positions: int = DEFAULT_TARGET_POSITIONS

    # Caps the live app lacks. Without max_position_fraction, ATR sizing
    # on a low-volatility name asks for multiples of the account.
    #
    # Applied in `risk` mode ONLY. Equal-weight already bounds each
    # position by construction, and applying both would silently re-impose
    # the throttle this mode exists to remove.
    max_positions: int = 20
    max_position_fraction: float = 0.20
    min_notional: float = 100.0
    allow_fractional: bool = True

    # Bars a held ticker may go missing before the position is force-sold
    # at its last known price rather than carried at a frozen mark.
    max_stale_bars: int = 5

    price_mode: PriceMode = "adjusted"
    risk_free_annual: float = 0.0

    # Interest earned on uninvested cash. Defaults to 0.0 so every result
    # recorded before this existed reproduces exactly — notably the 863.29%
    # Donchian run the regression test pins.
    #
    # It matters most for whatever is least invested: Donchian sits at ~98%
    # exposure and barely notices, while the RSI basket run averages under
    # a quarter deployed, where 0% on the rest is a large one-way
    # understatement across a decade that reached 5% short rates. Set it
    # equal to risk_free_annual so the equity curve and the Sharpe
    # denominator assume the same world.
    cash_yield_annual: float = 0.0

    label: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"end ({self.end}) must be after start ({self.start})")
        if not 0 < self.risk_fraction <= 1:
            raise ValueError("risk_fraction must be in (0, 1]")
        if self.initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        if not 0 < self.max_position_fraction <= 1:
            raise ValueError("max_position_fraction must be in (0, 1]")
        if self.max_positions < 1:
            raise ValueError("max_positions must be at least 1")
        if min(self.n_entry, self.m_exit, self.atr_period) < 2:
            raise ValueError("window lengths must be at least 2")
        if self.k_atr <= 0:
            raise ValueError("k_atr must be positive")
        if self.cost_bps < 0 or self.slippage_bps < 0:
            raise ValueError("costs cannot be negative")
        if self.strategy not in ("donchian", "rsi"):
            raise ValueError(f"unknown strategy {self.strategy!r}")
        if self.sizing_mode not in ("risk", "equal_weight"):
            raise ValueError(f"unknown sizing_mode {self.sizing_mode!r}")
        if self.target_positions < 1:
            raise ValueError("target_positions must be at least 1")
        if self.cash_yield_annual < 0:
            raise ValueError("cash_yield_annual cannot be negative")
        if min(self.rsi_period, self.rsi_trend_period, self.rsi_exit_ma_period) < 2:
            raise ValueError("RSI window lengths must be at least 2")
        if not 0 < self.rsi_entry_threshold < self.rsi_exit_threshold < 100:
            raise ValueError("RSI thresholds must satisfy 0 < entry < exit < 100")

    @property
    def variant(self) -> str:
        """Identifies which of the two compared systems this run measures."""
        return "atr_stop" if self.use_atr_stop else "faithful"

    @property
    def total_cost_rate(self) -> float:
        """Per-side cost as a fraction. Charged twice per round trip."""
        return (self.cost_bps + self.slippage_bps) / 10_000.0

    @property
    def warmup_bars(self) -> int:
        """Bars consumed before this strategy's first signal can fire.

        Strategy-dependent, and by a wide margin: Donchian needs 56 bars,
        while RSI(2) needs 201 because no entry is allowed until the
        200-day trend filter exists. The report prints it so a flat opening
        stretch of the equity curve reads as dead time rather than as the
        strategy declining to trade.

        Branching here rather than delegating to strategies.py keeps the
        dependency one-way — that module imports this one.
        """
        if self.strategy == "rsi":
            return max(
                self.rsi_trend_period,
                self.rsi_exit_ma_period,
                self.rsi_period,
                self.atr_period,
            ) + 1
        return max(self.n_entry, self.m_exit, self.atr_period) + 1
