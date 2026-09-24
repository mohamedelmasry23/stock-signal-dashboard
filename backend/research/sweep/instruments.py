"""What a "unit" of an instrument actually is.

The first version of the simulator charged costs as basis points of
`shares * price` and computed P&L as `shares * (exit - entry)`. Both are
correct for a share of an ETF and badly wrong for a futures contract,
which is the instrument this strategy is actually aimed at — and the error
runs in the direction that buries the strategy rather than flattering it,
which is how it was noticed.

One ES contract quoted at 4,950 is not $4,950 of exposure. It is
4,950 x $50 = $247,500, it moves $12.50 per 0.25-point tick, and it costs
roughly $2.50 of commission to trade round-turn rather than 10 bps of
notional (which would be $247). Charging notional basis points to a
5-minute strategy whose stop is two points turns a cost of about 0.2R into
a cost of 60R, and every result after that is a report on the cost model.

So every price-to-dollars conversion goes through this module:

    exposure   shares x price x point_value
    P&L        shares x point_value x (exit - entry)
    cost/side  shares x (commission + slippage_ticks x tick_size x point_value)
               + exposure x (cost_bps + slippage_bps) / 10_000

Both cost forms are available at once because both are real: a futures
broker charges per contract, an equity broker's slippage scales with size.
Leaving one at zero is how an instrument selects the model that fits it,
and the defaults are the equity ones so nothing silently changes shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Instrument:
    symbol: str
    #: Minimum price increment. Also what "one tick beyond the level"
    #: means in `SweepParams`, so the two must agree for a given symbol.
    tick_size: float = 0.01
    #: Dollars per 1.0 of price movement, per unit held. 1.0 for shares.
    point_value: float = 1.0
    #: Commission in dollars per unit per side.
    commission_per_unit: float = 0.0
    #: Slippage charged in ticks per side. The futures-shaped model:
    #: crossing a one-tick spread costs one tick regardless of size.
    slippage_ticks: float = 0.0
    #: Proportional friction per side, in basis points of exposure. The
    #: equity-shaped model, and the one every prior experiment used.
    cost_bps: float = 5.0
    slippage_bps: float = 5.0
    #: Annual borrow on short exposure. Zero for futures, where a short
    #: is just the other side of a contract and nothing is borrowed.
    borrow_annual: float = 0.005

    def exposure(self, shares: float, price: float) -> float:
        return shares * price * self.point_value

    def pnl(self, shares: float, entry: float, exit_price: float, sign: int) -> float:
        return sign * shares * self.point_value * (exit_price - entry)

    def cost(self, shares: float, price: float) -> float:
        """One side's friction, in dollars."""
        per_unit = self.commission_per_unit + self.slippage_ticks * self.tick_size * self.point_value
        proportional = self.exposure(shares, price) * (self.cost_bps + self.slippage_bps) / 10_000.0
        return shares * per_unit + proportional

    def risk_dollars(self, shares: float, risk_per_unit: float) -> float:
        return shares * risk_per_unit * self.point_value


#: Equity/ETF default — identical friction to every run recorded in
#: research/README.md, so a result here is comparable to those.
EQUITY = Instrument(symbol="_equity")

#: CME contract specs. Commission is a round-turn estimate split per side;
#: slippage is one tick per side, which is the optimistic-but-defensible
#: assumption for a liquid front-month contract and a limit entry.
FUTURES: Dict[str, Instrument] = {
    "ES=F": Instrument("ES=F", 0.25, 50.0, 1.25, 1.0, 0.0, 0.0, 0.0),
    "NQ=F": Instrument("NQ=F", 0.25, 20.0, 1.25, 1.0, 0.0, 0.0, 0.0),
    "GC=F": Instrument("GC=F", 0.10, 100.0, 1.25, 1.0, 0.0, 0.0, 0.0),
    "CL=F": Instrument("CL=F", 0.01, 1000.0, 1.25, 1.0, 0.0, 0.0, 0.0),
    "6E=F": Instrument("6E=F", 0.00005, 125_000.0, 1.25, 1.0, 0.0, 0.0, 0.0),
}


# The Databento continuous series are named by root (`NQ`, `ES`), not by
# Yahoo's `=F` suffix. Same contracts, same specs.
for _root in ("ES", "NQ", "GC", "CL"):
    FUTURES[_root] = Instrument(_root, *[getattr(FUTURES[f"{_root}=F"], f) for f in (
        "tick_size", "point_value", "commission_per_unit", "slippage_ticks",
        "cost_bps", "slippage_bps", "borrow_annual",
    )])


def resolve(symbol: str) -> Instrument:
    """The spec for a symbol, defaulting to the equity model.

    Defaulting rather than raising is deliberate: the harness must run on
    the daily equity cache today, long before any futures data exists. But
    a futures symbol that is not in `FUTURES` will silently be treated as
    a share, so `report.py` prints the resolved spec for every symbol it
    trades — the mistake is cheap to make and invisible in the result.
    """
    if symbol in FUTURES:
        return FUTURES[symbol]
    return Instrument(symbol=symbol)
