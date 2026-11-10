# Stock Signal Dashboard — Backend API + Pipeline

FastAPI + SQLite backend for the personal stock/commodities signal
dashboard, plus the daily pipeline that populates it. The React dashboard
that consumes this API lives in `../frontend`.

## Quickstart — run it yourself

```bash
cd backend

# 1. install dependencies (a virtualenv is recommended)
pip3 install -r requirements.txt

# 2a. RECOMMENDED: start from the shipped snapshot of the real dashboard —
#     real signals and every saved backtest, no holdings or account data.
#     Never overwrites an existing data/real.db. See app/public_snapshot.py.
python3 -m app.bootstrap
uvicorn app.main:app --reload --port 8000

# 2b. OR seed fake data: no network/API keys needed
DEMO_MODE=true python3 -m app.seed_demo
DEMO_MODE=true uvicorn app.main:app --reload --port 8000

# 2c. OR build real data from nothing: run the pipeline (needs internet —
#     pulls the S&P 500 list from Wikipedia and prices from Yahoo Finance).
#     After 2a, this is also how signals get refreshed to today.
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
(`COMMODITY_TICKERS`, all in `app/pipeline.py`)
+ anything currently in your `holdings` table (even if it's outside that
universe — a held ticker always gets its exit signal computed), fetches
2 years of daily history per ticker from `yfinance` in batches of 50, computes
signals via the same `compute_signal_frame()` the demo seeder uses, and
upserts into `data/real.db`.

Safe to re-run any time — it updates existing rows rather than duplicating
them. Refuses to run if `DEMO_MODE=true`, so it can never overwrite the
fake demo data with a giant list of real tickers.

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

### The S&P universe: the constituents workbook (every S&P run from 2026-09-14)

```bash
python3 -m app.backtest --universe constituents:data/universes/sp500_constituents.csv,etfs \
  --start 2016-01-01 --both-variants --save
```

Each company is tradeable only while it was an S&P 500 member, switched on
the **effective** date. Membership comes from a point-in-time constituents
workbook built from public sources
(`data/universes/sources/SP500_Historical_Constituents_2016_2025.xlsx`),
converted by `python3 -m app.backtest.sp500_constituents` into
`data/universes/sp500_constituents.csv` — one row per stretch of membership,
keyed by the ticker Yahoo prices the company under. 2026 continues from the
workbook's year-end list with the 2026 rows of `sp500_events.csv`, and the
result lands exactly on today's list (`sp500_anchor.txt`). Saved, this run
is the dashboard's headline; today's-list run is drawn dashed beside it.

Same bars as every run below (2016-01-04 to 2026-08-11), 10 bps round trip:

| | **Workbook membership** | + 2×ATR stop | Events membership (previous) | Today's list | SPY |
|---|---|---|---|---|---|
| Total return | **164%** | 209% | 177% | 863% | 353% |
| CAGR | **9.6%** | 11.2% | 10.1% | 23.8% | 15.3% |
| Max drawdown | **−28.5%** | −39.1% | −28.5% | −32.2% | −33.7% |
| Sharpe | **0.63** | 0.63 | 0.65 | 1.04 | 0.89 |
| Trades | 449 | 560 | 435 | 437 | — |

The conclusion doesn't move: **the strategy does not beat buy-and-hold on
the S&P 500**. The workbook and `sp500_events.csv` descend from the same
Wikipedia change log and agree on ~99% of company-days, so the 13-point
difference is mostly path dependence — with 20 slots, a handful of changed
members reshuffles which breakouts get capital. The differences that are
real, all checked by hand:

- **Effective, not announcement, dates.** A company joins the day it enters
  the index rather than the evening S&P announces it.
- **Eight removals the workbook's yearly sheets get wrong.** BorgWarner,
  Celanese, FMC and Teleflex left on 2025-03-24 but the 2025 sheet keeps
  them to 31 December; Vornado (2023), Tiffany (2021), WellCare (2020) and
  SCANA (2019) likewise. The workbook's own change log has the right dates,
  and `apply_change_log()` uses them.
- **DuPont is back.** The events replay dropped `DD` from September 2017 on
  (the old DuPont's removal and DowDuPont's rename collide on one ticker);
  the workbook keeps DowDuPont/DuPont a member throughout, which it was.
- **Ticker keying.** The workbook names companies as they were then; five
  renames it doesn't record are in `EXTRA_RENAMES` (BK→BNY, MMC→MRSH,
  EQR→VMRK, WYND→TNL, and IR→TT, split at 2020-03-02 when Gardner Denver
  took the IR symbol). Dr Pepper Snapple and Wyndham Worldwide now carry
  price history (KDP, TNL) for 2016–18 where the events file had none.

87 of the 707 companies still have no Yahoo data — the purged failures —
so this still leans optimistic. The workbook fixes *membership*; it can't
fix missing *prices*.

The ATR-stop variant ends higher here (209%) but at the same Sharpe and a
10-point deeper drawdown: more return for proportionally more risk, not a
better system.

`--index-overlay` still needs `events:data/universes/sp500_events.csv` —
the workbook has no announcement dates. The sections below describe the
earlier measurements and remain accurate for what they measured.

### Results (2016-01 to 2026-08, 10 bps round-trip costs)

*These are the earlier runs, kept because the gap between them is the
survivorship-bias measurement. The current headline is the
members-at-the-time run above: **164%** vs SPY's 353%.*

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

> **Superseded 2026-09-12.** A run on membership as it actually was, day
> by day, came in *below* the 2016 "floor" — see [Membership as it
> actually was](#membership-as-it-actually-was). The bracket reasoning
> doesn't hold for a strategy that picks 20 names out of 500.

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
  --start 2016-01-01 --offline --save
```

The list is committed at `data/universes/sp500_2016.txt` with its Wayback
source URL in the header, so the run is reproducible without re-scraping.

`--save` is what puts this run on the dashboard's equity chart, as a
dashed line beside the current-list run — the visible width of the
bracket. The API keys it off the stored universe label (`sp500_2016,etfs`,
written by `universe_label()` in `app/backtest/__main__.py`), so a
point-in-time run is returned as its own series instead of replacing the
run it exists to be compared against. Renaming the file to something
without a four-digit year would silently turn it back into an ordinary
run: `is_point_in_time()` in `app/config.py` is the one definition, and
both the report's caveats and the dashboard's read from it.

### Membership as it actually was

```bash
python3 -m app.backtest --universe events:data/universes/sp500_events.csv,etfs \
  --index-overlay --offline
```

Each company is tradeable only while it was in the S&P 500, switching on
the day S&P **announced** the change. `--index-overlay` runs it twice:
once as that membership filter alone, and once also buying each addition at
the open after its announcement and selling any holding at the open after
its removal is announced — additions then held until Donchian's own exit.
Same bars as above (2016-01-04 to 2026-08-11), 10 bps round trip:

| | Today's list | 2016 list | **Actual membership** | + announcement overlay | SPY |
|---|---|---|---|---|---|
| Total return | 870% | 202% | **177%** | 141% | 353% |
| CAGR | 23.9% | 11.0% | **10.1%** | 8.7% | 15.3% |
| Max drawdown | −32.2% | −52.0% | **−28.5%** | −30.6% | −33.7% |
| Sharpe | 1.05 | 0.47 | **0.65** | 0.56 | 0.89 |
| Trades | 441 | 439 | 435 | 466 | — |

(Today's list reads 870% rather than 863% — same bars, so most likely
because the list itself has changed since August: Reddit and Ferguson in,
AvalonBay and Electronic Arts out.)

**The strategy does not beat buy-and-hold on the S&P 500.** 177% is the
best estimate available from free data, and it still leans optimistic:
91 of the 712 companies that were members at some point since 2016 have
no Yahoo data, and the missing ones are disproportionately the failures.
So the true figure is most likely lower still. It isn't an artefact of the
start date either — the gap holds from every start tried:

| Start | Actual membership | SPY | Sharpe (strategy / SPY) |
|---|---|---|---|
| 2016-01 | 177% | 353% | 0.65 / 0.89 |
| 2017-01 | 159% | 296% | 0.65 / 0.88 |
| 2018-01 | 115% | 226% | 0.58 / 0.82 |

**The 2016 "floor" wasn't a floor.** Nothing guarantees that two wrong
lists bound the right one when the strategy only ever holds ~20 names out
of ~500: which companies are in the pool changes which breakouts win a
slot, and the whole path after that. The replay also shows that 36 of the
"186 removed tickers" counted above are companies that changed ticker —
most still in the index (FB → META, PCLN → BKNG, UTX → RTX, …) — which
Yahoo keys under the new symbol, so the 2016 run simply lost them. The
purged-data problem is real, but smaller than that count made it look.

**The announcement overlay makes it worse** — by 35 points and 0.09 of
Sharpe here, and in every variation tried: from all three start dates
(−35, −54, −39 points) and at three smaller position sizes (−12 to −20).
Where it goes:

- **It can rarely afford the trade.** Of 208 addition announcements, 139
  found no cash: Donchian sits ~97% invested. 39 more were untradeable —
  16 with no Yahoo data at all, 23 spin-offs with no price history yet on
  the announcement day. 30 were bought, 21 of those smaller than intended.
- **The ones it buys don't pay.** 30 trades, 43% won, average −0.51% —
  against +1.78% for Donchian's own entries. And they spend cash a
  breakout would have used: signals skipped for capital rose from 784 to
  1,158.
- **Removals barely come up.** One holding was sold on a removal
  announcement: Donchian rarely owns a company at the moment S&P drops it.
- **The feared failure mode was rare.** Donchian's exit is a state (close
  below the 20-day low), so an addition bought below that level is sold
  the next bar. It happened once in 30.

What it cannot measure: S&P announces after the close, so the earliest
honest fill is the next open, after the overnight gap where much of the
announcement move happens. That part of the pop is out of reach of a
daily-bar backtest and, for a retail account, of the trade itself.

**Where the dates come from.** `data/universes/sp500_events.csv` holds
231 changes since 2016, each with its announcement date and the source of
that date: 221 from S&P's own press archive, 10 from S&P documents cited on
Wikipedia, none guessed. Where the archive and Wikipedia both had a date
they agreed on all but three: two Wikipedia rows cite the wrong release,
and one cites an S&P document dated the day before its release. Fourteen rows are
renames recorded as replacements (FleetCor → Corpay, …) and are marked
`continuation` so they re-key the ticker instead of trading. The file is
replayed backwards from today's list (`sp500_anchor.txt`), because a
forward replay from 2016 would lose every ticker change — and that replay
reproduces the independently archived `sp500_2016.txt` exactly, up to the
36 ticker changes, Fossil (whose replacement was announced before the
start), and one collision: Dow Chemical and the old DuPont both resolve to
`DD`, two companies under one Yahoo ticker. That cross-check is pinned
by `test_the_committed_file_replays_to_the_archived_2016_list`.

Rebuild both files (a few minutes; about 230 polite requests to S&P's
archive) with `python3 -m app.backtest.sp500_events`, then drop any row
from `PENDING` that the Wikipedia table has caught up with.

**On the dashboard.** Saved without `--index-overlay`, the membership run
is drawn as the bold "Members at the time" line on the backtest chart,
with today's list dashed and the 2016 list dotted beside it:

```bash
python3 -m app.backtest --universe events:data/universes/sp500_events.csv,etfs \
  --offline --save
```

The API keys it off the `sp500_events` label (`is_rolling_membership()` in
`app/config.py`) and serves it as its own `membership` series, so it never
displaces the today's-list run the headline figures come from. `--save`
is still refused with `--index-overlay`: the dashboard has no place for
that run, and the live pipeline has no announcement feed to trade it.

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

### The intraday watchlist, and why it isn't a strategy

`scripts/watch_scan.sh` runs during the US session and fills a separate
`watch_items` table with whatever is trading above its 55-day high right now.
It never writes to `signals`.

That separation is measured, not stylistic. Across 52,087 breakouts in the
cached decade:

| | share | entering earlier vs the next-open fill |
|---|---|---|
| Gapped through the level at the open | 22.7% | **+0.96%** mean |
| Crossed during the session | 77.3% | **+0.04%** mean |

For the 77% where an intraday scan is the only way to see the move early,
acting on it is worth four basis points — less than one side of costs. So
there is no measured edge in reacting intraday, and the panel exists to show
rather than to decide. The case where timing genuinely pays is the gap, and a
gap is visible at 09:30 without any scanning at all.

Two implementation notes that are easy to get wrong:

The level is recomputed per scan rather than read from `signals.donchian_high`.
The pipeline stores the level *that* session's close had to beat; the live
session needs one that includes the last completed session's high. They differ
on 15.5% of ticker-days, and on 2.37% the stored one would list a ticker that
is not actually above its level — roughly a third of a median day's list of 24.

The scan refuses outside 09:30–16:05 ET before making any network call. After
16:05 the "in progress" bar is a finished one, and a level computed from it
would silently include today's own high.

It runs `scripts/daily_pipeline.sh`, which writes **one line per run**
saying whether that run was healthy:

```
2026-08-14 23:30:48 EEST  ok      donchian 517 updated, 0 failed; 505 equities
2026-08-13 19:39:51 EEST  WARNING 267 tickers failed — unusually high
2026-08-18 11:02:13 EEST  ok      donchian 517 updated, 0 failed; 505 equities; mid-session run, partial bar skipped
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

## Live quotes

The pipeline is end-of-day, but the dashboard can show what a position is
worth *right now*. Passing `?live=true` to `/api/holdings` or
`/api/account` fetches current quotes from Yahoo for held tickers only
(`app/quotes.py`), and those drive:

- each holding's **day-change percentage** — taken from Yahoo's own
  `regularMarketChangePercent` rather than derived here, so it matches
  what you'd see quoted anywhere else, and Yahoo's handling of halts,
  splits and odd sessions comes along with it
- each holding's **market value**, and therefore account equity and every
  suggested position size

**Live prices never touch the signals.** Donchian levels, ATR and the exit
flag stay computed from completed daily closes. An exit that fired on an
intraday dip which recovered by the close would be a different strategy
from the one the backtest measured, and the dashboard would stop
describing the system those numbers came from.
`test_live_prices_do_not_touch_the_signals` pins this.

Yahoo being slow or down degrades to the stored closes rather than
breaking the page. Quotes are cached for 10 seconds — just long enough to
stop one dashboard refresh fetching the same tickers twice (it calls two
endpoints), short enough that clicking Refresh again genuinely re-fetches.

Note the units: Yahoo reports `0.5265777` to mean **0.53%**, already
scaled as a percentage. Multiplying it by 100 "to convert" renders a
half-percent day as a 53% move.

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
| GET    | `/api/holdings`             | All holdings, each with its latest signal (exit flag included). `?live=true` adds current Yahoo quotes |
| POST   | `/api/holdings`             | Add a holding: `{"ticker": "...", "shares": optional}`           |
| PATCH  | `/api/holdings/{ticker}`    | Update shares on an existing holding                             |
| DELETE | `/api/holdings/{ticker}`    | Remove a holding                                                 |
| GET    | `/api/entry-candidates`     | Top N (`?limit=`, max 10) fresh entry signals, excluding held tickers, ranked by ATR-normalized breakout strength |
| GET    | `/api/account`              | Account equity, its source, and the inputs behind it. `?live=true` values holdings at current quotes |
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

