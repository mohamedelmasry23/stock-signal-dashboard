"""Turning a run into something a person can judge.

Deliberately plain text on stdout, like `app/backtest/report.py`. The
point of this harness is a verdict written into `research/README.md`, and
a format that pastes into markdown without reformatting is worth more
here than anything richer.

Three things this prints that an ordinary performance table does not, each
because a run can look fine without them:

* **The funnel.** A strategy that finds nothing and a strategy whose fifth
  component discards everything look identical from the return alone, and
  they call for opposite responses.
* **The realised risk fraction.** On a strategy with a few-tick stop, the
  exposure caps bind long before `risk_fraction` is reached, so each trade
  may risk a hundredth of what the config asked for. The return is then
  arithmetically tiny for a reason that has nothing to do with the edge.
* **The instrument spec actually used.** `instruments.resolve` falls back
  to the equity model for anything it does not recognise, so a futures
  symbol with a typo trades as a share of stock at 1/50th the exposure and
  nothing else in the output says so.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .instruments import Instrument
from .params import SweepParams
from .simulate import SimResult


def _pct(value: Optional[float], places: int = 2) -> str:
    return "—" if value is None else f"{value * 100:.{places}f}%"


def _num(value: Optional[float], places: int = 2) -> str:
    return "—" if value is None else f"{value:.{places}f}"


def format_summary(result: SimResult, title: str = "") -> str:
    s = result.summary
    lines: List[str] = []
    if title:
        lines += [f"# {title}", ""]
    lines += [
        f"{s.get('start')} to {s.get('end')}  ({s.get('bars')} trading days)",
        "",
        f"  Total return      {_pct(s.get('total_return')):>12}",
        f"  CAGR              {_pct(s.get('cagr')):>12}",
        f"  Max drawdown      {_pct(s.get('max_drawdown')):>12}",
        f"  Volatility (ann.) {_pct(s.get('volatility')):>12}",
        f"  Sharpe            {_num(s.get('sharpe')):>12}",
        f"  Sortino           {_num(s.get('sortino')):>12}",
        "",
        f"  Trades            {s.get('n_trades'):>12}   ({s.get('n_long')} long / {s.get('n_short')} short)",
        f"  Win rate          {_pct(s.get('win_rate'), 1):>12}",
        f"  Profit factor     {_num(s.get('profit_factor')):>12}",
        f"  Avg win / loss R  {_num(s.get('avg_win_r')):>5} / {_num(s.get('avg_loss_r')):<5}",
        f"  Costs paid        {_num(s.get('gross_costs'), 0):>12}",
        "",
    ]

    target = s.get("target_risk_fraction")
    realised = s.get("avg_risk_fraction")
    lines.append("## Sizing reality check")
    lines.append(f"  Risk per trade: asked for {_pct(target)}, actually risked {_pct(realised, 4)}")
    if realised is not None and target and realised < target / 5:
        lines.append(
            "  WARNING: the exposure caps bound every trade far below the risk "
            "target, so this return is not comparable to a daily system's."
        )
    lines.append("")
    return "\n".join(lines)


def format_funnel(funnel: Dict[str, int]) -> str:
    """Where the trade count went, component by component."""
    if not funnel:
        return ""
    sweeps = funnel.get("sweeps", 0)
    setups = funnel.get("setups", 0)
    lines = ["## Funnel", "", f"  sweeps detected          {sweeps:>8}"]
    for key, value in sorted(funnel.items()):
        if key in ("sweeps", "setups"):
            continue
        share = f"{value / sweeps * 100:5.1f}%" if sweeps else "    —"
        lines.append(f"  rejected: {key:<22} {value:>8}  {share}")
    lines += [f"  setups produced          {setups:>8}", ""]
    return "\n".join(lines)


def format_outcomes(outcomes: Dict[str, int]) -> str:
    if not outcomes:
        return ""
    lines = ["## What happened to the setups", ""]
    for key, value in sorted(outcomes.items()):
        lines.append(f"  {key:<24} {value:>8}")
    lines.append("")
    return "\n".join(lines)


def format_instruments(specs: Iterable[Instrument]) -> str:
    lines = ["## Instruments as resolved", ""]
    lines.append(f"  {'symbol':<10} {'tick':>10} {'point $':>10} {'comm/side':>10} {'slip tk':>8} {'bps/side':>9}")
    for spec in specs:
        lines.append(
            f"  {spec.symbol:<10} {spec.tick_size:>10g} {spec.point_value:>10g} "
            f"{spec.commission_per_unit:>10g} {spec.slippage_ticks:>8g} "
            f"{spec.cost_bps + spec.slippage_bps:>9g}"
        )
    lines.append("")
    return "\n".join(lines)


def format_params(params: SweepParams) -> str:
    lines = ["## Parameters", ""]
    for field, value in sorted(vars(params).items()):
        lines.append(f"  {field:<24} {value}")
    lines.append("")
    return "\n".join(lines)


def format_sensitivity(rows: Sequence[Tuple[str, object, dict]]) -> str:
    """One row per parameter value, grouped by parameter.

    The verdict line under each group is the whole reason this table
    exists. An edge should show as a PLATEAU across neighbouring settings;
    a single value that works while its neighbours do not is a fit to this
    particular data, and saying so in the output makes it harder to read
    the best row and stop there.
    """
    if not rows:
        return ""
    lines = ["## Sensitivity", ""]
    grouped: Dict[str, List[Tuple[object, dict]]] = {}
    for name, value, summary in rows:
        grouped.setdefault(name, []).append((value, summary))

    for name, entries in grouped.items():
        lines.append(f"### {name}")
        lines.append("")
        lines.append(f"  {'value':<12} {'return':>10} {'Sharpe':>8} {'maxDD':>9} {'trades':>7} {'win':>7}")
        returns = []
        for value, summary in entries:
            total = summary.get("total_return")
            returns.append(total)
            lines.append(
                f"  {str(value):<12} {_pct(total):>10} {_num(summary.get('sharpe')):>8} "
                f"{_pct(summary.get('max_drawdown')):>9} {summary.get('n_trades', 0):>7} "
                f"{_pct(summary.get('win_rate'), 0):>7}"
            )
        lines.append("")
        lines.append(f"  {_verdict(returns)}")
        lines.append("")
    return "\n".join(lines)


def _verdict(returns: Sequence[Optional[float]], baseline: Optional[float] = None) -> str:
    """Shape, not ranking.

    An edge shows as a plateau: neighbouring parameter values agree about
    the sign and rough size of the effect. A single value that works while
    its neighbours do not is a fit to this data.

    When a `baseline` is given the judgement is made on the DIFFERENCE
    from it, which is the only meaningful reading for a variant of an
    existing system. Without that, a set of variants all returning around
    150% reads as a comfortable plateau even when the system they modify
    returns 164% and they are simply noise around it — which is exactly
    what the first version of this function reported for the sweep veto.
    """
    clean = [r for r in returns if r is not None]
    if len(clean) < 3:
        return "too few settings to judge shape."

    if baseline is not None:
        deltas = [r - baseline for r in clean]
        better = [d for d in deltas if d > 0]
        worse = [d for d in deltas if d < 0]
        if not better:
            return "every setting was worse than the baseline — the rule does not help."
        if better and worse:
            return (
                f"the effect changes sign between neighbouring settings "
                f"({len(better)} better, {len(worse)} worse, best "
                f"{max(deltas) * 100:+.0f}pp, worst {min(deltas) * 100:+.0f}pp) — "
                "noise around the baseline, not an edge."
            )
        return (
            "every setting beat the baseline — a plateau of improvement, "
            "which is what an edge looks like."
        )

    best = max(clean)
    positive = [r for r in clean if r > 0]
    if best <= 0:
        return "every setting lost — no edge to curve-fit."
    if len(positive) == 1:
        return (
            "ONE setting is positive and its neighbours are not — a spike, "
            "not a plateau. Read this as curve-fitting."
        )
    second = sorted(clean, reverse=True)[1]
    if second <= 0 or best > 3 * max(second, 1e-9):
        return (
            "the best setting is far above the next one — a spike, not a "
            "plateau. Read this as curve-fitting."
        )
    return "results hold across neighbouring settings — a plateau, which is what an edge looks like."
