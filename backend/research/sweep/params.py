"""Every threshold this strategy has, in one place.

The spec this harness codifies is explicit that it is replacing a
discretionary judgement with fixed numbers, and that a result which only
appears at one exact setting is curve-fitting rather than an edge. That
only stays checkable if no threshold is buried in a function body, so:
every number the rules depend on is a field here, and `sweepcli.py --sweep`
walks them.

Defaults are the spec's own stated values where it states one, and
otherwise the most common published setting — never a value chosen after
seeing a result on this data. When that changes, it must be said out loud
in `research/README.md`, because a fitted default silently turns an
out-of-sample test into an in-sample one.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Tuple

#: Level families the strategy is allowed to fade. Named so a run can be
#: restricted to one family and the others measured out — the cheapest way
#: to learn whether the edge, if any, lives in session levels or in swings.
ALL_LEVEL_TYPES: Tuple[str, ...] = (
    "prev_day_high",
    "prev_day_low",
    "asia_high",
    "asia_low",
    "london_high",
    "london_low",
    "swing_high",
    "swing_low",
)


@dataclass(frozen=True)
class SweepParams:
    # --- timeframes --------------------------------------------------
    #: The native feed. Everything else is resampled from it, which is
    #: what makes "read a higher-timeframe bar before it closed"
    #: unrepresentable rather than merely tested against.
    execution_timeframe: str = "5min"
    #: Where directional bias is read from (component 2).
    bias_timeframe: str = "4h"
    #: Where swing liquidity levels are read from (component 1).
    structure_timeframe: str = "1h"
    #: 18:00 ET, the CME session open. See bars.py — this decides what
    #: "previous day" means, so it is a parameter, not a constant.
    day_boundary_hour: int = 18

    # --- component 1: liquidity levels -------------------------------
    level_types: Tuple[str, ...] = ALL_LEVEL_TYPES
    #: Bars either side of a pivot before it counts as confirmed. The
    #: right-hand side is the lag: a pivot with a 3-bar lookback is not
    #: knowable until 3 bars after it printed.
    pivot_lookback: int = 3
    #: How long a level stays interesting. An untouched level from three
    #: weeks ago is still "obvious", but the premise is about stops
    #: resting NOW, so an unbounded memory is a different claim.
    level_max_age_days: float = 5.0

    # --- component 2: directional bias -------------------------------
    #: False measures the no-bias variant the spec asks for: does the
    #: filter add edge, or only cut trade count?
    use_bias: bool = True

    # --- component 3: the sweep --------------------------------------
    #: "Beyond the level by at least one tick." Instrument-specific, so
    #: the runner resolves it per symbol; this is the fallback.
    tick_size: float = 0.01
    #: How far beyond the level price must trade, in ticks.
    sweep_penetration_ticks: float = 1.0

    # --- component 4: the structure shift ----------------------------
    #: Execution bars allowed between the sweep and the shift. Too wide
    #: and any later move counts as confirmation of any earlier sweep.
    sweep_window: int = 12
    #: "Decisive body": body >= this fraction of the candle's range.
    min_body_frac: float = 0.5

    # --- component 5: the entry imbalance ----------------------------
    #: Minimum gap width, in ticks. A one-tick gap is noise, not
    #: displacement.
    min_fvg_ticks: float = 2.0
    #: Where in the gap the limit rests. 0.0 = the near (proximal) edge,
    #: filled first and most often; 1.0 = the far edge, a better price
    #: that fills less often. The trade-off between fill rate and R is
    #: exactly the kind of thing that must be swept, not assumed.
    fvg_entry_depth: float = 0.0
    #: Bars a resting limit order stays live before it is cancelled. The
    #: spec is explicit that a missed trade is correct behaviour.
    entry_expiry_bars: int = 24

    # --- exits --------------------------------------------------------
    #: Stop goes this many ticks beyond the sweep's extreme wick.
    stop_buffer_ticks: float = 1.0
    #: Setups whose target is nearer than this are skipped: the premise is
    #: asymmetric payoff, not win rate.
    min_r: float = 1.5

    def replaced(self, **changes) -> "SweepParams":
        return replace(self, **changes)

    def __post_init__(self) -> None:
        # Cheap guards against the configurations that would produce
        # confident nonsense rather than an error.
        if self.pivot_lookback < 1:
            raise ValueError("pivot_lookback must be at least 1 — a pivot needs a side")
        if not 0.0 <= self.fvg_entry_depth <= 1.0:
            raise ValueError("fvg_entry_depth is a fraction of the gap, so 0.0..1.0")
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive")
        if self.sweep_window < 1:
            raise ValueError("sweep_window must allow at least one bar")
        unknown = set(self.level_types) - set(ALL_LEVEL_TYPES)
        if unknown:
            raise ValueError(f"Unknown level types: {', '.join(sorted(unknown))}")

    @property
    def penetration(self) -> float:
        """Price distance that counts as "beyond" a level."""
        return self.sweep_penetration_ticks * self.tick_size

    @property
    def stop_buffer(self) -> float:
        return self.stop_buffer_ticks * self.tick_size

    @property
    def min_fvg_size(self) -> float:
        return self.min_fvg_ticks * self.tick_size


#: The grid `--sweep` walks. One parameter moved at a time from the
#: default, because the question being asked is whether a result survives
#: its neighbours — a full cross-product answers a different question and
#: costs exponentially more.
SENSITIVITY_GRID: dict[str, Iterable] = {
    "pivot_lookback": (2, 3, 5, 8),
    "sweep_window": (6, 12, 24, 48),
    "min_body_frac": (0.0, 0.3, 0.5, 0.7),
    "min_fvg_ticks": (0.0, 1.0, 2.0, 4.0, 8.0),
    "min_r": (1.0, 1.5, 2.0, 3.0),
    "fvg_entry_depth": (0.0, 0.25, 0.5, 1.0),
    "entry_expiry_bars": (6, 12, 24, 48),
    "use_bias": (True, False),
    "level_max_age_days": (1.0, 2.0, 5.0, 10.0),
}
