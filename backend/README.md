# Stock Signal Dashboard — Backend API + Pipeline

FastAPI + SQLite backend for the personal stock/commodities signal
dashboard, plus the daily pipeline that populates it. The React dashboard
that consumes this API lives in `../frontend`.

## Quickstart — run it yourself

```bash
cd backend

# 2. install dependencies (a virtualenv is recommended)
pip3 install -r requirements.txt

# 3a. EASIEST WAY TO LOOK AROUND: seed fake data, no network/API keys needed
DEMO_MODE=true python3 -m app.seed_demo
DEMO_MODE=true uvicorn app.main:app --reload --port 8000

# 3b. OR, for real data: run the pipeline once (needs internet — pulls
#     the S&P 500 list from Wikipedia and prices from Yahoo Finance),
#     then start the API against real.db
python3 -m app.pipeline
uvicorn app.main:app --reload --port 8000
```

Then open `http://localhost:8000/docs` — FastAPI's auto-generated Swagger
UI, where you can call every endpoint (including adding/removing holdings)
straight from the browser without writing any curl commands. `/api/holdings`
and `/api/entry-candidates` are the two to check first.

Note: the pipeline (3b) takes a few minutes on first run — it's fetching
~500 tickers' worth of 2-year daily history from Yahoo in batches, with a
short pause between batches to stay polite to their rate limits.

## Populate data

**Demo (fake data, safe to publish alongside a public demo later):**

```bash
DEMO_MODE=true python3 -m app.seed_demo
```

Creates `data/demo.db` with 10 fake tickers, 3 fake holdings, and computed
signals — including at least one exit signal on a held ticker and a few
entry candidates, so every endpoint has something to return without
touching the network.

**Real (live data):**

```bash
python3 -m app.pipeline
```

Scans the S&P 500 (pulled fresh from Wikipedia) + a few extra equity ETFs
(`EXTRA_EQUITY_TICKERS`) + 12 commodity tickers spanning precious metals,
energy, industrial metals, agriculture and a broad basket
(`COMMODITY_TICKERS`, both in `app/pipeline.py`)
+ anything currently in your `holdings` table (even if it's outside that
universe — a held ticker always gets its exit signal computed), fetches
2 years of daily history per ticker from `yfinance` in batches of 50, computes
signals via the same `compute_signal_frame()` the demo seeder uses, and
upserts into `data/real.db`. Safe to re-run any time — it updates existing
rows rather than duplicating them. Refuses to run if `DEMO_MODE=true`, so
it can never overwrite the fake demo data with a giant list of real tickers.

Since holdings start empty on a fresh install, run `python -m app.pipeline`
once first to populate the universe, then add your holdings through
`POST /api/holdings` (or the `/docs` UI), then re-run the pipeline so
those tickers' exit signals get computed too.

## Holding bullion (and anything quoted per unit)

`shares` on a holding means "however many units the ticker is quoted in",
not strictly shares. That distinction matters for physical metal:

| What you own | Ticker | Put in `shares` |
|---|---|---|
| Gold bullion by weight | `GC=F` | troy ounces |
| A gold ETF | `GLD` | shares |

`GC=F` is COMEX gold, quoted in USD **per troy ounce**, so 3 oz entered as
`shares: 3` values correctly and gets a real exit signal on the gold
price. Entering bullion as `GLD` instead would misprice it — a GLD share
is a fraction of an ounce, and that fraction shrinks over time as the
fund's expenses accrue, so any fixed conversion you pick goes stale.

Two caveats worth knowing. `GC=F` is the front-month futures contract, so
it trades a little away from spot (usually well under 1% for gold) —
fine for trend signals and close enough for valuation, but not a precise
mark. And a dealer's buy/sell spread and storage fees mean your
realisable value is a bit below the screen price either way.

## Backtesting

```bash
python3 -m app.backtest --universe commodities,etfs --start 2016-01-01 --both-variants --save
```

Replays the strategy over historical prices and reports what would have
happened. `--save` persists the result so the dashboard displays it;
artifacts (`equity.csv`, `trades.csv`, `summary.md`) go to
`data/backtests/`. Bars are cached in `data/prices_cache.db`, so re-runs
need no network — add `--offline` to guarantee it.

### Results (2016-01 to 2026-08, 10 bps round-trip costs)

| | S&P 500 (today's list) | S&P 500 (2016 list) | Commodities + ETFs | SPY buy & hold |
|---|---|---|---|---|
| Total return | 863% | **202%** | 148% | 353% |
| CAGR | 23.8% | 11.0% | 9.0% | 15.3% |
| Max drawdown | −32.2% | **−52.0%** | −28.5% | −33.7% |
| Volatility | 23.2% | 34.2% | 17.5% | 17.8% |
| Sharpe | 1.04 | **0.47** | 0.58 | 0.89 |
| Trades | 437 | 439 | 213 | 1 |
| Win rate | 44.9% | 41.0% | 46.5% | — |
| Profit factor | 3.11 | 1.45 | 2.09 | — |

**The first two columns are the same strategy over the same decade.** The
only difference is which companies it was allowed to trade: today's index
membership, versus membership as it stood in December 2015. That single
change turns a strategy that beats SPY by 500 points into one that loses
to it by 150, and nearly doubles the worst drawdown.

That gap *is* the survivorship bias. See below for why neither column is
the truth.

### Bracketing the truth

Neither S&P run is unbiased — they're wrong in opposite directions:

- **Today's list** only contains companies that survived and stayed in the
  index, so the strategy is never given the chance to buy a disaster. It
  **overstates**.
- **The 2016 list** puts 186 since-removed companies back in (37% of the
  index), but excludes the 185 added afterwards — including several of the
  decade's biggest winners. It **understates**.

So the honest conclusion is that the true result lies somewhere between
**202% and 863%**. SPY's 353% sits inside that range, which means:

> **This backtest cannot determine whether the strategy beats buy-and-hold.**

That is a real finding, not a failure. It's a far more useful thing to
know than a single confident number that happens to be wrong.

### How partial the mitigation is

The 2016 run is better, not fixed. Of the 186 removed tickers, **only 84
(45%) still have usable price data** — the other 102 return nothing from
Yahoo at all. The failures that matter most, the outright bankruptcies,
are precisely the ones free data sources have purged. That is why
survivorship-free datasets (CRSP, Norgate, Sharadar) are sold
commercially.

Regenerate the snapshot with:

```bash
python3 -m app.backtest.universe 20160101 sp500_2016
python3 -m app.backtest --universe file:data/universes/sp500_2016.txt,etfs \
  --start 2016-01-01 --both-variants
```

The list is committed at `data/universes/sp500_2016.txt` with its Wayback
source URL in the header, so the run is reproducible without re-scraping.

### What the numbers actually say

**The win rate is supposed to be under 50%.** Trend following loses often
and wins big: average win 2.5R against average loss 0.7R is where the
profit factor of 3.1 comes from. A high win rate would be the surprising
result, not this.

**The strategy's own risk model is wrong.** It sizes every position so one
stop distance costs 1% of equity — but its only exit is the Donchian
breakdown, with no stop order behind it. Measured: **23.7%** of losing
trades lost more than the 1% they were sized to risk, and the worst lost
**5.7×** it. "Risking 1%" is a claim the simulation falsifies.

**Adding the missing stop makes it worse, not better.** The comparison run
places a hard 2×ATR stop:

| | As built | With 2×ATR stop |
|---|---|---|
| Total return | 863% | 167% |
| Max drawdown | −32.2% | −35.6% |
| Sharpe | 1.04 | 0.56 |
| Losses worse than 1R | 23.7% | 18.5% |
| Trades | 437 | 671 |

The stop does control tail losses, but it stops out of positions that
would have recovered, and the extra 234 round trips cost more than the
protection is worth. Worth knowing before "fixing" the missing stop.

**Costs matter more than anything else here.** Same universe, varying only
the friction:

| Cost per side | Total return |
|---|---|
| 0 bps | 167% |
| 5 bps | 148% |
| 25 bps | 86% |
| 50 bps | 29% |

At 213 trades, a half-percent round trip removes most of the edge. If
you're trading a small account with currency conversion on USD tickers,
your real costs may be closer to the bottom row than the top.

### Why these numbers are optimistic

- **Survivorship bias.** `get_sp500_tickers()` returns *today's* index
  membership, so the S&P run tests only companies that both survived and
  stayed in the index. This is worth several points a year of fake alpha.
  The commodities/ETFs run has no constituent-selection bias, which is why
  it's the more honest of the two — and it *underperformed* SPY.
- **The comparison isn't like-for-like.** The commodities run is measured
  against SPY because that's the reference everyone knows, but its
  universe is mostly commodities. Some of that gap is asset class, not
  strategy.
- **Concentration.** The S&P run skipped 68,361 signals for lack of
  capital — it can only hold 20 positions out of ~500 candidates, so the
  result leans heavily on the ranking picking well.
- **Ten years is one sample.** It covers one long bull market, one crash
  and one inflation shock. A 55-day breakout system trades slowly; this
  is not many independent observations.

### Design notes

- **Look-ahead is structurally prevented.** Each step reads signals from
  the *decision* bar and fills on the *execution* bar. `compute_signal_frame`
  is reused unchanged so the backtest can't drift from the live dashboard,
  and `test_truncation_invariance` fails if any future value leaks.
- **Prices are total-return.** Dividends are ~40% of long-run equity
  return (measured: KO 96% price-only vs 168% with dividends over 10y),
  and the benchmark is adjusted identically.
- **`GC=F` is excluded from trading.** Its roll gaps are contract changes,
  not P&L. It stays in the live dashboard for *valuing* bullion.
- Historical bars live in `data/prices_cache.db`, deliberately separate
  from `real.db` — see the isolation note in `app/backtest/prices.py`.

## Schedule it (once daily, after market close)

The pipeline is meant to run once per day, a bit after the US market
closes (4:00pm ET) — that's when the close is finalized and the levels
for the next session are locked in. A ~30-60 min buffer is a reasonable
safety margin for data availability.

**macOS — use launchd, not cron.** cron silently *skips* a job if the Mac
was asleep at the scheduled time and never makes it up; launchd runs it on
the next wake instead. The US close is late evening across Europe and
Africa, so the laptop is usually shut when the job is due — that
distinction decides whether it ever runs at all.

`scripts/com.stock-tracker.pipeline.plist.example` is a working agent.
Copy it to `~/Library/LaunchAgents/`, replace the placeholder paths, then:

```bash
launchctl load ~/Library/LaunchAgents/com.yourname.stock-tracker.pipeline.plist
launchctl start com.yourname.stock-tracker.pipeline   # force a run to test
tail -3 backend/pipeline.log
```

It runs `scripts/daily_pipeline.sh`, which writes **one line per run**
saying whether that run was healthy:

```
2026-08-13 19:42:55 EEST  ok      517 updated, 0 failed, 505 equities
2026-08-13 19:39:51 EEST  WARNING 267 tickers failed — unusually high
```

The wrapper exists because the pipeline is deliberately forgiving — a
failed S&P fetch logs a warning and carries on with commodities only — so
a scheduled job piping raw output to a file nobody reads would hide
exactly the failure that matters most. It flags an empty equity universe,
an unusually high failure count, a crash, and a non-zero exit, and appends
the full output for any non-ok run.

### Three macOS traps this setup works around

Each one made the scheduled run fail while the identical command succeeded
from a terminal — the hardest kind of problem to debug, and the reason the
plist carries the settings it does.

1. **TCC.** A launchd agent cannot read `~/Desktop`, `~/Documents` or
   `~/Downloads`. The job dies with "Operation not permitted" before it
   can log anything at all. Keep the project elsewhere; this one lives
   under `~/Projects`.
2. **File descriptors.** launchd gives a task **256**; an interactive
   shell here gets over a million. yfinance fetches 50 tickers per chunk
   across threads, and the sockets plus its SQLite cache blow past 256 —
   SQLite reports that as
   `OperationalError('unable to open database file')`, which looks nothing
   like the real cause. Hence `SoftResourceLimits → NumberOfFiles: 8192`.
   This alone failed ~270 of 517 tickers.
3. **yfinance's cache location.** It defaults to
   `~/Library/Caches/py-yfinance`, which the agent couldn't open.
   `configure_yfinance_cache()` redirects it into `data/yf_cache/`.

**Linux (cron)** — cron is fine on a server, which doesn't sleep:

```
30 21 * * 1-5 /path/to/backend/scripts/daily_pipeline.sh
```

**Windows (Task Scheduler):** create a Basic Task → Trigger: Daily,
weekdays, ~4:30pm local time → Action: Start a program →
`C:\path\to\venv\Scripts\python.exe` with argument
`-m app.pipeline` and "Start in" set to the `backend` folder.

## Run

```bash
DEMO_MODE=true uvicorn app.main:app --reload --port 8000   # demo data
# or
uvicorn app.main:app --reload --port 8000                  # real data
```

Docs at `http://localhost:8000/docs`.

## Configuration

All settings are environment variables. The defaults are the right ones
for localhost and the wrong ones for a deploy — see `DEPLOY.md`.

| Variable | Default | Purpose |
|---|---|---|
| `DEMO_MODE` | `false` | serve `demo.db` instead of `real.db` |
| `DATABASE_URL` | `sqlite:///data/<db>` | override for a mounted disk or another engine |
| `ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | comma-separated CORS allowlist |
| `API_KEY` | unset | when set, writes require a matching `X-API-Key` header |
| `READ_ONLY` | `false` | when true, all writes return 403 regardless of `API_KEY` |

The API prints which database and write policy are active at startup:

```
[api] database: real | writes: OPEN — no API_KEY set, safe only if this is localhost-only
[api] CORS allow_origins: ['http://localhost:5173', 'http://127.0.0.1:5173']
```

That line exists because every one of these is an env var, so a
misconfigured instance is otherwise indistinguishable from a correct one
until something goes wrong.

Note that an API key shipped in a browser bundle is not secret — it raises
the bar against drive-by scanners and nothing more. For a public demo the
honest control is `READ_ONLY`, which has no secret to leak.

## Tests

```bash
pip3 install -r requirements-dev.txt
python3 -m pytest tests/ -q
```

50 tests, no network access required. They point `DATABASE_URL` at a
temporary file before importing anything from `app`, so the suite can
never touch `real.db` — `app.main` calls `create_all()` at import time,
so that ordering is load-bearing.

## Endpoints

| Method | Path                      | Purpose                                                        |
|--------|---------------------------|------------------------------------------------------------------|
| GET    | `/api/health`              | Liveness check, reports `demo_mode`                             |
| GET    | `/api/status`               | When signals were last computed (for a "stale data" warning)     |
| GET    | `/api/holdings`             | All holdings, each with its latest signal (exit flag included)  |
| POST   | `/api/holdings`             | Add a holding: `{"ticker": "...", "shares": optional}`           |
| PATCH  | `/api/holdings/{ticker}`    | Update shares on an existing holding                             |
| DELETE | `/api/holdings/{ticker}`    | Remove a holding                                                 |
| GET    | `/api/entry-candidates`     | Top N (`?limit=`, max 10) fresh entry signals, excluding held tickers, ranked by ATR-normalized breakout strength |
| GET    | `/api/account`              | Account equity, its source, and the inputs behind it              |
| PATCH  | `/api/account`              | Set `cash_balance`, `risk_fraction`, or `equity_override`        |

## Schema

- **`holdings`** — what you currently own (ticker + optional amount, in whatever unit the ticker is quoted in)
- **`universe`** — the scan universe (ticker + asset class: equity/commodity) — refreshed each pipeline run
- **`signals`** — latest Donchian/ATR snapshot per ticker — upserted daily by the pipeline
- **`account_settings`** — single row: cash balance, risk fraction, optional equity override

`signals.prev_close` holds the prior session's close so the day's move can
be shown without refetching history. It's null for a ticker with a single
bar — an unknown move, which the UI renders as `—` rather than 0.00%.

### Schema changes on an existing database

`create_all()` only creates missing *tables*; it will not add a column to
a table that already exists. So `sync_new_columns()` (in
`app/database.py`) runs at startup and adds any new **nullable** column
the models declare but the database lacks, logging each one:

```
[db] added missing column signals.prev_close
```

That's the whole scope — it cannot rename, drop, re-type or backfill, and
it deliberately skips non-nullable columns since there'd be no valid value
for existing rows. Anything beyond adding a nullable column needs a real
migration tool (Alembic); don't grow this function into one.

## Position sizing / account equity

The strategy sizes each position so a stop-out costs a fixed fraction of
the account:

```
shares = (risk_fraction x equity) / stop_distance     # stop_distance = 2 x ATR
```

`stop_distance` is a market fact the pipeline computes. **`equity` is
not** — it's how much money you have, which changes when you deposit,
withdraw or trade without any price moving. So equity is resolved at
*read* time (`app/account.py`), not frozen into the `signals` table.
That's why editing your cash balance re-sizes every suggestion
immediately instead of waiting for the next daily run.

Equity resolves in three tiers:

1. **`equity_override`** if set — you state the number explicitly.
2. Otherwise **derived**: market value of holdings (shares x last close)
   plus `cash_balance`. Change a holding and the next read reflects it,
   with no pipeline run in between.
3. Otherwise **default** ($100k) — the fresh-install fallback, so an empty
   database still shows plausible numbers. `/api/account` reports
   `equity_source: "default"` so the UI can flag it as a placeholder
   rather than presenting it as real.

Holdings with no share count, or with no computed close yet, can't be
valued; they're excluded from the total and reported as
`unvalued_holdings` rather than silently counted as zero.

The pipeline also restates `signals.suggested_shares` at the end of each
run, so the stored column stays meaningful if you query the database
directly. The API's read-time figure is the authoritative one.

### Timestamps

All `DateTime` columns store **naive UTC**. SQLAlchemy's SQLite dialect
drops `tzinfo` on write and reads back naive regardless of what you pass
in (even with `DateTime(timezone=True)`), so `app/timeutil.py`'s
`utcnow()` makes that convention explicit rather than passing aware
values that get silently flattened — that's what keeps in-memory and
DB-loaded values the same type.

The API serializes them with an explicit `+00:00` offset
(`UtcDateTime` in `app/schemas.py`). Don't remove that: a bare
`2026-08-11T18:50:26` is interpreted by JavaScript's `new Date()` as
*local* time, which would silently skew every timestamp in the frontend
and break the stale-data check against `/api/status`.

## Files

- `app/signals.py` — the Donchian/ATR calculation itself, unchanged from the original brief script, factored out so both the pipeline and the demo seeder call one source of truth.
- `app/timeutil.py` — `utcnow()`, the single source of truth for timestamps (see "Timestamps" above).
- `app/account.py` — account equity resolution and position sizing (see "Position sizing" above).
- `app/pipeline.py` — the daily job: fetch → compute → upsert.
- `app/seed_demo.py` — synthetic-data stand-in for the pipeline, used for `DEMO_MODE` and local testing without hitting the network.
- `app/main.py` — the FastAPI app and all endpoints.

## Notes

- CORS is wide open (`allow_origins=["*"]`) for local dev — tighten this
  before any public deploy.
- `entry-candidates` ranks by `(close - donchian_high) / ATR` — how many
  ATRs past the breakout level today's close printed. It's a reasonable
  proxy for signal strength, not part of the original strategy spec, so
  feel free to swap it for something else.
- Strategy constants (55/20-day windows, ATR(20), 2x ATR) are fixed in
  `app/signals.py`'s defaults, matching the brief's "fixed for v1"
  decision. Risk fraction and account equity are **not** fixed — they're
  account settings (see "Position sizing" above). The `equity` default on
  `compute_signal_frame()` exists only so that function stays testable on
  a bare DataFrame; don't read `Shares` off that frame and treat it as a
  real position size.
- The pipeline pulls the S&P 500 list from Wikipedia via `requests` +
  `pandas.read_html`; if that page's structure ever changes and the fetch
  fails, it logs a warning and continues with just the commodity tickers
  + your holdings rather than crashing the whole run. **That fallback is
  quiet by design, so check the run summary line** — `503 equities + 11
  commodities` means it worked; `0 equities` means only commodities were
  scanned. The request sends a descriptive `User-Agent` because Wikipedia
  returns `403 Forbidden` to urllib's default one.
- Several commodity ETFs track the same underlying complex (`USO`/`BNO`
  are both crude; `DBC` is energy-weighted), so one oil move can surface
  multiple correlated entry candidates and crowd out the top-10 list.
  Trim `COMMODITY_TICKERS` if that becomes annoying.

