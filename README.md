# Stock Signal Dashboard

A personal trading-signal dashboard built around a Donchian channel
breakout system. It tracks what you hold, tells you when a position has
broken its exit level, and surfaces the strongest fresh breakouts across
the S&P 500 and a set of commodity ETFs — sized to your actual account.

![Python](https://img.shields.io/badge/python-3.12+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-backend-009688)
![React](https://img.shields.io/badge/React-19-61dafb)
![Tests](https://img.shields.io/badge/tests-91%20passing-brightgreen)

## What it does

A daily job pulls the current S&P 500 constituent list from Wikipedia and
two years of daily prices for ~514 tickers from Yahoo Finance, then
computes three levels per ticker:

| Level | Meaning |
|---|---|
| 55-day Donchian high | close above it → **entry signal** (breakout) |
| 20-day Donchian low | close below it → **exit signal** |
| ATR(20) | volatility, used for stop distance (2×ATR) and position sizing |

The dashboard then shows:

- **Exit signals on your own holdings**, surfaced first and in red — the
  thing you actually need to act on.
- **The top 10 entry candidates**, ranked by how many ATRs past its
  breakout level each close printed, excluding what you already own.
- **Position sizes** computed so a stop-out costs a fixed fraction of your
  equity.

Holdings are entered manually. The amount is recorded in whatever unit
the ticker is quoted in, so bullion held by weight (`GC=F`, quoted per
troy ounce) values correctly alongside ordinary shares.

## Does it work?

There's a backtester, so this is measured rather than asserted
(2016–2026, dividends reinvested, 10 bps round-trip costs):

| | S&P 500 + ETFs | Commodities + ETFs | SPY buy & hold |
|---|---|---|---|
| Total return | 863% | 148% | 353% |
| CAGR | 23.8% | 9.0% | 15.3% |
| Max drawdown | −32.2% | −28.5% | −33.7% |
| Sharpe | 1.04 | 0.58 | 0.89 |
| Win rate | 44.9% | 46.5% | — |

**The honest reading is more interesting than the headline.** The S&P
number carries survivorship bias — the universe is *today's* index
membership, so it only tests companies that survived and stayed in.
The commodities run has no such bias, and it lost to buy-and-hold.

Two findings worth more than the returns:

- **The strategy's own risk model is wrong.** It sizes each position so a
  stop-out costs 1% of equity, but its only exit is the Donchian
  breakdown — there's no stop order. Measured: 23.7% of losing trades lost
  more than the 1% they were sized to risk; the worst lost 5.7× it.
- **Adding the missing stop makes it worse.** A hard 2×ATR stop cuts
  return from 863% to 167% and deepens max drawdown, because it exits
  positions that would have recovered and pays for 234 extra round trips.

Full numbers, cost sensitivity and caveats: [backend/README.md](backend/README.md#backtesting).

## Design notes

A few decisions that aren't obvious from the file listing:

**Position sizing is resolved at read time, not stored.** `stop_distance`
is a market fact the pipeline computes; equity is not — it changes when
you deposit, withdraw or trade, with no price moving. Keeping them apart
means editing your cash balance re-sizes every suggestion immediately
instead of waiting for the next daily run. Equity resolves in three tiers
(explicit override → holdings + cash → a labelled placeholder) so the
number on screen is never unexplained.

**A holding's amount is in the ticker's own quote unit.** Gold bullion is
tracked as `GC=F` (COMEX gold, quoted per troy ounce) with ounces in the
amount field, so `amount × price` is the real dollar value. Recording it
as a share count of a gold ETF would drift, because an ETF share is a
fraction of an ounce that shrinks as the fund's expenses accrue.

**Timestamps are stored naive-UTC but served with an explicit `+00:00`.**
SQLAlchemy's SQLite dialect strips tzinfo on write regardless, and a bare
timestamp is read as *local* time by JavaScript's `Date()` — which would
silently skew the frontend's stale-data check by the UTC offset.

**Write access has three postures**, chosen by environment: open
(localhost), API-key required (private deploy), or fully read-only (public
demo). The API prints which one is active at startup, because a
misconfigured instance otherwise looks identical to a correct one.

**The backtester reuses the live signal code rather than reimplementing
it.** A backtest with its own copy of the strategy stops describing the
system it's meant to measure the moment either side changes. Look-ahead is
prevented structurally — signals are read from the *decision* bar and
fills happen on the *execution* bar — and a truncation-invariance test
fails if any future value leaks into an earlier equity point.

## Layout

```
backend/    FastAPI + SQLite: daily signal pipeline, backtester, REST API
frontend/   React + TypeScript + Tailwind v4 dashboard
```

Each has its own README with setup, endpoints, and the reasoning behind
the trickier parts.

## Quickstart

Demo mode needs no network and no API keys — it seeds a fake database so
every screen has something to show:

```bash
cd backend && pip3 install -r requirements.txt
DEMO_MODE=true python3 -m app.seed_demo
DEMO_MODE=true python3 -m uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:5173. For real market data, run
`python3 -m app.pipeline` (a few minutes; needs internet) and start the
backend without `DEMO_MODE`.

## Tests

```bash
cd backend && pip3 install -r requirements-dev.txt && python3 -m pytest tests/ -q
```

91 tests covering the signal math (including that the Donchian windows are
shifted so today's high can't be part of the level it must break), the
equity resolution tiers, per-ounce holdings, the backtest engine
(look-ahead detection, capital rationing, off-by-one), and the API — auth
postures and validation included. No network access required.

## Disclaimer

This is a personal tool for tracking a mechanical signal system. It is not
investment advice, and the suggested position sizes are arithmetic, not a
recommendation.
