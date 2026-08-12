"""Backtest parameters. Pure data — no I/O, no imports from the engine."""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional

# Strategy defaults deliberately mirror app/signals.py's, so an unmodified
# run measures the system the dashboard actually implements. Every one is
# overridable from the CLI for sensitivity work.
DEFAULT_N_ENTRY = 55
DEFAULT_M_EXIT = 20
DEFAULT_ATR_PERIOD = 20
DEFAULT_K_ATR = 2.0

FillTiming = Literal["next_open", "next_close"]
PriceMode = Literal["adjusted", "raw"]


@dataclass(frozen=True)
class BacktestConfig:
    start: date
    end: date

    tickers: tuple[str, ...] = ()
    benchmark: str = "SPY"

    initial_equity: float = 100_000.0
    risk_fraction: float = 0.01

    # Strategy windows — passed straight through to compute_signal_frame.
    n_entry: int = DEFAULT_N_ENTRY
    m_exit: int = DEFAULT_M_EXIT
    atr_period: int = DEFAULT_ATR_PERIOD
    k_atr: float = DEFAULT_K_ATR

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

    # Caps the live app lacks. Without max_position_fraction, ATR sizing
    # on a low-volatility name asks for multiples of the account.
    max_positions: int = 20
    max_position_fraction: float = 0.20
    min_notional: float = 100.0
    allow_fractional: bool = True

    # Bars a held ticker may go missing before the position is force-sold
    # at its last known price rather than carried at a frozen mark.
    max_stale_bars: int = 5

    price_mode: PriceMode = "adjusted"
    risk_free_annual: float = 0.0

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
        """Bars consumed before any signal can fire. The equity curve is
        flat over this stretch, so the report trims it rather than showing
        a misleading run-up of dead time."""
        return max(self.n_entry, self.m_exit, self.atr_period) + 1
