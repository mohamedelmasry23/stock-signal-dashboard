"""The five components assembled into setups.

Reading order, which is also the order a trade comes into existence:

    bias (2)    is the higher timeframe pointing this way at all?
    sweep (3)   an obvious level broken and rejected  -> the setup
    shift (4)   control actually flipped               -> the confirmation
    gap (5)     where the limit rests                  -> the entry
    levels (1)  the next pool, which is the target

A `Setup` is a complete resting order: limit, stop, target and an expiry,
all fixed at the moment the gap completed. Nothing about it is revised
afterwards, which is what makes it simulable without further judgement —
and what makes every rejection here countable, so `rejections` can say
which component actually decides the trade count.

## The timestamps

`valid_from` is the close of the bar AFTER the structure shift. The gap
search looks at bars up to that one — it is the bar that completes the
shift candle's own gap, the usual shape — so which gap gets chosen is not
final until it has closed, even when the answer is an earlier gap. That is
also how the setup is traded: see the shift candle close, wait one candle
to see whether it leaves an imbalance, then place the limit. A shift on
the very last bar of the data is therefore undecided, not a setup. The simulator will not look at a setup before this instant,
and that single field is where this module's half of the look-ahead
contract lives.

The first version used the gap's `known_at` alone. That was a look-ahead
bug, and a flattering one: the gap search may return a gap that completed
BEFORE the shift (an earlier gap in the same leg), and 25% of NQ setups
did. Their limit orders then rested before the confirmation existed and
filled on pullbacks that were selected, in effect, for being followed by a
break of structure. It scaled with `sweep_window` — the wider the window,
the earlier the order could go live — which is how it was found: the
sensitivity grid showed a looser confirmation rule earning ten times more
(+25R at 12 bars, +248R at 48), and a looser rule should not be better.
`test_no_setup_goes_live_before_its_structure_shift` pins it.

## Where the target comes from

The next opposing pool, on the same logic that drove the entry: price
reaches for stops. It is taken from the level book as of `valid_from`, so
a level that had already been mitigated is not offered as a destination.

Setups whose target is nearer than `min_r` are dropped rather than traded
smaller. The strategy's expectancy is meant to come from asymmetric
payoff, so a 0.8R version of it is a different strategy wearing the name.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from .bars import Timeframes, build_timeframes, known_at
from .bias import BiasSeries, Direction, build_bias
from .fvg import FairValueGap, find_entry_gap
from .levels import LevelBook, LiquidityLevel, build_levels
from .mss import StructureShift, find_structure_shift
from .params import SweepParams
from .sweep import Sweep, find_sweeps
from .swings import PivotSeries, find_pivots


@dataclass(frozen=True)
class Setup:
    direction: Direction
    entry_limit: float
    stop: float
    target: float
    #: When the order may first rest. Candle 3's close.
    valid_from: pd.Timestamp
    #: When an unfilled order is cancelled — counted in BARS, so a
    #: weekend does not silently expire an order that had hours left.
    expires_at: pd.Timestamp
    reward_risk: float
    sweep: Sweep
    shift: StructureShift
    gap: FairValueGap
    target_level: LiquidityLevel

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry_limit - self.stop)


@dataclass
class StrategyContext:
    """Everything one instrument's run produced, kept for reporting.

    The intermediates are not debris: `rejections` is the only way to tell
    a strategy that finds nothing from a strategy whose fifth component
    throws everything away, and those call for opposite responses.
    """

    symbol: str
    params: SweepParams
    timeframes: Timeframes
    levels: LevelBook
    bias: BiasSeries
    sweeps: List[Sweep]
    setups: List[Setup]
    rejections: Counter = field(default_factory=Counter)

    def funnel(self) -> Dict[str, int]:
        """Trade count attrition, component by component."""
        out = {"sweeps": len(self.sweeps)}
        out.update({k: int(v) for k, v in sorted(self.rejections.items())})
        out["setups"] = len(self.setups)
        return out


def build_context(symbol: str, frame: pd.DataFrame, params: SweepParams) -> StrategyContext:
    """Run the whole pipeline for one instrument."""
    timeframes = build_timeframes(
        frame,
        execution=params.execution_timeframe,
        higher=(params.structure_timeframe, params.bias_timeframe),
        day_boundary_hour=params.day_boundary_hour,
    )
    execution = timeframes.execution

    # Three pivot sets, on three timeframes, for three different jobs:
    # execution pivots confirm the structure shift, structure-timeframe
    # pivots become liquidity levels, bias-timeframe pivots define trend.
    execution_pivots = PivotSeries(
        find_pivots(execution, params.pivot_lookback, params.execution_timeframe)
    )
    structure_frame = timeframes.get(params.structure_timeframe)
    structure_pivots = find_pivots(
        structure_frame, params.pivot_lookback, params.structure_timeframe
    )
    bias_frame = timeframes.get(params.bias_timeframe)
    bias_pivots = PivotSeries(
        find_pivots(bias_frame, params.pivot_lookback, params.bias_timeframe)
    )

    levels = build_levels(execution, structure_pivots, params)
    bias = BiasSeries(
        build_bias(bias_frame, bias_pivots, params.bias_timeframe),
        enabled=params.use_bias,
    )

    sweeps = find_sweeps(execution, levels, params)
    setups, rejections = _setups_from(execution, sweeps, execution_pivots, levels, bias, params)

    return StrategyContext(
        symbol=symbol,
        params=params,
        timeframes=timeframes,
        levels=levels,
        bias=bias,
        sweeps=sweeps,
        setups=setups,
        rejections=rejections,
    )


def _setups_from(
    execution: pd.DataFrame,
    sweeps: List[Sweep],
    pivots: PivotSeries,
    levels: LevelBook,
    bias: BiasSeries,
    params: SweepParams,
):
    index = execution.index
    last = len(index) - 1
    setups: List[Setup] = []
    rejections: Counter = Counter()

    for sweep in sweeps:
        # The bias is read as of the sweep's close, not the entry's, so a
        # flip that happens while the limit order rests cannot
        # retroactively qualify a setup that was not allowed when it formed.
        if not bias.allows(sweep.known_at, sweep.direction):
            rejections["bias_disallowed"] += 1
            continue

        shift = find_structure_shift(execution, sweep, pivots, params)
        if shift is None:
            rejections["no_structure_shift"] += 1
            continue
        if shift.bar_index + 1 > last:
            # The bar that decides which gap to use does not exist yet.
            rejections["undecided_at_data_end"] += 1
            continue

        gap = find_entry_gap(execution, sweep.direction, sweep.bar_index, shift.bar_index, params)
        if gap is None:
            rejections["no_fair_value_gap"] += 1
            continue

        setup = _assemble(sweep, shift, gap, levels, params, index, last)
        if isinstance(setup, str):
            rejections[setup] += 1
            continue
        setups.append(setup)

    setups.sort(key=lambda s: (s.valid_from, s.entry_limit))
    return setups, rejections


def _assemble(
    sweep: Sweep,
    shift: StructureShift,
    gap: FairValueGap,
    levels: LevelBook,
    params: SweepParams,
    index: pd.DatetimeIndex,
    last: int,
):
    """A setup, or the name of the rule that rejected it."""
    entry = gap.entry_limit(params.fvg_entry_depth)
    # The gap choice is final only once the bar after the shift has closed.
    # See "The timestamps" in the module docstring.
    live_bar = shift.bar_index + 1
    valid_from = known_at(index[live_bar], params.execution_timeframe)

    if sweep.direction == "bullish":
        stop = sweep.extreme - params.stop_buffer
        if entry <= stop:
            # The gap sits at or below the swept low: there is no trade
            # here, only an arithmetic accident. Worth counting rather
            # than clamping, because a run where this is common means the
            # displacement is being detected in the wrong place.
            return "entry_through_stop"
        level = levels.nearest_above(entry, valid_from)
        if level is None:
            return "no_target_pool"
        target = level.price
    else:
        stop = sweep.extreme + params.stop_buffer
        if entry >= stop:
            return "entry_through_stop"
        level = levels.nearest_below(entry, valid_from)
        if level is None:
            return "no_target_pool"
        target = level.price

    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0:
        return "zero_risk"
    reward_risk = reward / risk
    if reward_risk < params.min_r:
        return "below_min_r"

    # Counted from when the order went live, not from when the gap formed:
    # otherwise a gap that predates its shift could expire before the
    # order was ever allowed to rest.
    expires_at = index[min(live_bar + params.entry_expiry_bars, last)]

    return Setup(
        direction=sweep.direction,
        entry_limit=float(entry),
        stop=float(stop),
        target=float(target),
        valid_from=valid_from,
        expires_at=expires_at,
        reward_risk=float(reward_risk),
        sweep=sweep,
        shift=shift,
        gap=gap,
        target_level=level,
    )
