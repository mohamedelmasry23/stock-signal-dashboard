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

## Schedule it (once daily, after market close)

The pipeline is meant to run once per day, a bit after the US market
closes (4:00pm ET) — that's when the close is finalized and the levels
for the next session are locked in. A ~30-60 min buffer is a reasonable
safety margin for data availability.

**macOS/Linux (cron)** — run `crontab -e` and add (adjust the path and
time; this example is 4:30pm ET, i.e. 21:30 UTC — check current UTC
offset since it shifts with US daylight saving):

```
30 21 * * 1-5 cd /path/to/backend && /path/to/venv/bin/python -m app.pipeline >> pipeline.log 2>&1
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

