# Stock Signal Dashboard

A personal trading-signal dashboard built around a Donchian channel
breakout system. It tracks what you hold, tells you when a position has
broken its exit level, and surfaces the strongest fresh breakouts across
the S&P 500 and a set of commodity ETFs — sized to your actual account.

![Python](https://img.shields.io/badge/python-3.12+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-backend-009688)
![React](https://img.shields.io/badge/React-19-61dafb)
![Tests](https://img.shields.io/badge/tests-51%20passing-brightgreen)

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

Holdings come from manual entry or from a Wealthsimple activity CSV
dropped into `backend/data/imports/`, which the daily job picks up
automatically.

## Design notes

A few decisions that aren't obvious from the file listing:

**Position sizing is resolved at read time, not stored.** `stop_distance`
is a market fact the pipeline computes; equity is not — it changes when
you deposit, withdraw or trade, with no price moving. Keeping them apart
means editing your cash balance re-sizes every suggestion immediately
instead of waiting for the next daily run. Equity resolves in three tiers
(explicit override → holdings + cash → a labelled placeholder) so the
number on screen is never unexplained.

**CSV imports are a full resync, not a merge.** Net positions from the
export become the entire holdings table, so a position you've sold
disappears instead of lingering. Columns are matched by alias rather than
a fixed schema, because Wealthsimple doesn't publish a stable one.

**Timestamps are stored naive-UTC but served with an explicit `+00:00`.**
SQLAlchemy's SQLite dialect strips tzinfo on write regardless, and a bare
timestamp is read as *local* time by JavaScript's `Date()` — which would
silently skew the frontend's stale-data check by the UTC offset.

**Write access has three postures**, chosen by environment: open
(localhost), API-key required (private deploy), or fully read-only (public
demo). The API prints which one is active at startup, because a
misconfigured instance otherwise looks identical to a correct one.

## Layout

```
backend/    FastAPI + SQLite: signal pipeline, CSV import, REST API
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

51 tests covering the signal math (including that the Donchian windows are
shifted so today's high can't be part of the level it must break), CSV
net-position parsing, the equity resolution tiers, and the API — auth
postures and validation included. No network access required.

## Disclaimer

This is a personal tool for tracking a mechanical signal system. It is not
investment advice, and the suggested position sizes are arithmetic, not a
recommendation.
