# Research experiments

Strategy ideas tested against the Donchian system **as research only**. None
of them is implemented in the app, and none was adopted: every one did worse
than the long-only system it was compared with.

The scripts for experiments 1-6 were one-off files kept outside the repository,
so they are not committed. This file records what each one did and what it
found, so the experiments don't need to be re-run to know the answer.

**Experiment 7 is different**: it is committed, under `backend/research/sweep/`,
with tests in `backend/tests/test_sweep_*.py`. It needed a second simulator
rather than a copy of the engine loop (limit orders, an intrabar stop *and*
target, shorts, and 5-minute bars are all outside what `app/backtest/engine.py`
does), and it is waiting on an intraday data feed, so throwing it away and
writing the finding down was not an option.

## How every experiment was run

- **Same engine, extended.** Each script copied the backtest loop from
  `backend/app/backtest/engine.py` and added the new rule. Before any result
  was trusted, the copy was run with the new rule switched off and checked
  against the real `run_backtest`: every script matched the engine's equity
  curve to **$0.00**.
- **Imported, not duplicated:** signals come from
  `get_strategy("donchian").frame` (the one strategy implementation), prices
  from the backtest cache, metrics from `app/backtest/metrics.py`.
- **Universe:** S&P 500 companies only while they were members
  (`constituents:data/universes/sp500_constituents.csv,etfs`), or SPY alone
  where stated.
- **Period:** 2016-01-04 to 2026-08-11 (the cached bars).
- **Costs:** 5 bps commission + 5 bps slippage per side, as in the dashboard
  runs. Shorts also pay a 0.5% a year borrow fee. Prices are
  dividend-adjusted, so a short pays the dividends it owes.
- **Sizing:** the dashboard's rules (1% of equity risked per position ÷
  2×ATR, capped at 20% of equity, no margin) unless marked **all-in**
  (the whole account in one position, used for SPY alone).
- **Signals:** a *buy signal* is a close above the prior 55-day high; a
  *sell signal* is a close crossing below the prior 20-day low — the same
  sell markers the ticker chart draws. Every decision is made on a close and
  filled at the next open.

Reference points: the long-only system on the S&P universe returned
**+164%** (Sharpe 0.63, max drawdown −28.5%); **SPY buy & hold returned
+353%** (Sharpe 0.89, −33.7%).

---

## 1. Short after two sell signals, cover on a buy signal

*Script: `short_after_two_sells.py` · 2026-09-14*

**Rule.** After the second sell signal since the last buy signal, short the
stock at the next open. Further sells don't close it. Cover at the next open
after a buy signal, which is also where the long side re-enters. Shorts only
while the company is an index member; longs and shorts share the 20 slots and
the account's cash.

| Run | Total | Max DD | Sharpe | Shorts | Short win rate |
|---|---|---|---|---|---|
| Long only | **+164%** | −28.5% | 0.63 | — | — |
| **Long + short** | **−45%** | −54.9% | −0.23 | 118 | 20% |
| Short leg alone | −86% | −86.6% | −0.88 | 533 | 29% |
| "Two sells" = two closes in a row below the 20-day low | −45% | −56.0% | −0.22 | 105 | 15% |
| Short leg alone, two closes in a row | −90% | −90.7% | −1.02 | 542 | 27% |
| Long + short with margin (gross up to 200%) | −70% | −73.8% | −0.21 | 154 | 21% |
| Short leg alone, no borrow fee | −80% | −80.6% | −0.64 | 532 | 29% |
| Short alone, cover at the 20-day high instead (diagnostic) | −83% | −83.0% | −0.73 | 1,116 | 31% |
| Long + short, cover at the 20-day high (diagnostic) | −23% | −37.0% | −0.04 | 126 | 22% |

**Finding.** Every variant lost money overall. In the long + short run the
shorts lost in every calendar year from 2016 to 2026, including 2020 and 2022;
the short-only runs managed a profitable year only here and there. They also starved the long side of capital: long profits fell
from $163k to $26k. Covering sooner (at the 20-day high) still lost, so the
entry is a problem, not only the slow cover.

## 2. The same rules on SPY alone

*Script: `spy_only.py` · 2026-09-16*

The Donchian system and the short rule from experiment 1, run on SPY as a
single instrument, at dashboard sizing and at all-in sizing.

| Run (all-in) | Total | Max DD | Sharpe | Time in market |
|---|---|---|---|---|
| SPY buy & hold | **+353%** | −33.7% | 0.89 | 100% |
| Donchian long only | +53% | −25.0% | 0.49 | 57% |
| Long + short after 2 sells | −47% | −56.3% | −0.32 | 82% |
| Short rule alone | −66% | −65.8% | −0.76 | 25% |
| "Two sells" = two closes in a row | −46% | −55.0% | −0.30 | 86% |

At dashboard sizing the account is only ~12% invested, and the same order holds
at smaller size: long only +9.5%, long + short −10.7%, short alone −18.4%.
Paying 2% a year on idle cash lifts all-in long-only to +67%.

**Finding.** All 16 SPY shorts lost. That is structural, not bad luck: a short
is covered on a close above the 55-day high, and if that comes within 55
trading days of the entry, the entry day is still inside the window, so the
cover price is above the entry price. A short can only win if held longer
than 55 days, and none of the 16 came out ahead. The long-only system on SPY
makes money but is out of the market 43% of the time and misses most of the
index's gain.

## 3. Cover shorts on abnormal volume — SPY

*Script: `spy_volume_cover.py` · 2026-09-16*

**Rule.** Same entry (second sell signal since the last buy). Cover when a
close comes on volume above the **52-week average volume** (252 sessions,
excluding that day), tested two ways — volume OR a buy signal, and volume
only — plus 1.5× and 2× the average as stricter thresholds. After a cover, a
new short needs a fresh sell signal. SPY's volume beats its 52-week average on
36.5% of days.

| Cover when (all-in) | Total | Max DD | Sharpe | Shorts | Won | Avg hold |
|---|---|---|---|---|---|---|
| No shorts (long only) | **+53%** | −25.0% | 0.49 | — | — | — |
| Buy signal (experiment 2) | −47% | −56.3% | −0.32 | 16 | 0% | 42 days |
| **Volume > avg, or buy** | **+36%** | −34.3% | 0.33 | 38 | 42% | 1.7 days |
| **Volume > avg only** | +23% | −34.3% | 0.24 | 38 | 42% | 2.8 days |
| Volume > 1.5× avg, or buy | +16% | −23.1% | 0.18 | 30 | 40% | 5.6 days |
| Volume > 1.5× avg only | −25% | −39.1% | −0.18 | 29 | 41% | 13 days |
| Volume > 2× avg, or buy | −18% | −39.8% | −0.08 | 25 | 28% | 14 days |
| Volume > 2× avg only | −67% | −70.1% | −0.67 | 20 | 40% | 62 days |

At dashboard sizing: long only +9.5%, volume-or-buy +6.7%, volume only +4.6%.

**Finding.** The volume cover limits the damage mainly by closing shorts
almost immediately — breakdowns usually come on heavy volume, so the cover
condition is often already true on entry day. Broadly, the longer a threshold lets
shorts run, the worse the result. No version beats long-only. (The 52-week average needs
a year of history, so the volume cover could not fire during 2016.)

## 4. Cover shorts on abnormal volume — S&P stocks

*Scripts: `stocks_volume_cover.py`, `stocks_volume_frictionless.py` · 2026-09-16*

Experiment 3's rules on the S&P universe at dashboard sizing. The cover-on-buy
runs reproduced experiment 1 exactly.

| Cover when | Total | Max DD | Sharpe | Shorts | Won | Avg hold | Short P&L |
|---|---|---|---|---|---|---|---|
| No shorts (long only) | **+164%** | −28.5% | 0.63 | — | — | — | — |
| Buy signal (experiment 1) | −45% | −54.9% | −0.23 | 118 | 20% | 82 days | −$70.4k |
| **Volume > avg, or buy** | **+50%** | −31.7% | 0.31 | 272 | 46% | 5.6 days | −$17.6k |
| **Volume > avg only** | +8% | −41.1% | 0.13 | 261 | 46% | 12 days | −$33.8k |
| Volume > 1.5× avg, or buy | −1% | −38.3% | 0.08 | 227 | 40% | 11 days | −$47.3k |
| Volume > 1.5× avg only | −30% | −49.7% | −0.10 | 224 | 43% | 20 days | −$60.0k |
| Volume > 2× avg, or buy | −31% | −45.9% | −0.14 | 196 | 37% | 20 days | −$50.5k |
| Volume > 2× avg only | −20% | −55.8% | −0.02 | 196 | 46% | 36 days | −$61.6k |

Short leg alone: −97% (volume or buy), −96% (volume only), −86% (buy signal).

**Finding.** Shorts lost in every version. Adding them cost far more than
their own losses — in the best version shorts lost $17.6k but total return
fell from +164% to +50%, because slots and cash spent on shorts crowded out
the long breakouts (long profit $163k → $67k). **Costs are not the cause:**
with zero costs and no borrow fee, short-alone still lost −79% (profit factor
0.84) and long + short reached only +73%. Stocks that break below the 20-day
low on heavy volume bounced about as often as they kept falling.

## 5. Sell the weakest holding to fund a stronger breakout

*Script: `swap_weakest.py` · 2026-09-16*

**Rule.** The system runs ~97% invested and skips ~2,150 signals for lack of
cash. When a breakout can't be funded in full, sell the weakest holding at the
same open and use the proceeds. "Weakest", measured on the decision bar, three
ways: **momentum** (close − 55-day high) ÷ ATR, the score new breakouts are
ranked by; **nearest to exit** (close − 20-day low) ÷ ATR; **worst unrealised
return**. The breakout's momentum must beat the holding's by a margin of 0,
0.5, 1 or 2 ATR.

**Unrestricted** (any breakout day, any holding except one bought that
morning): 2,852–16,728 swaps and returns of **−9% to −96%**. A stock signals
every day it stays above its 55-day high, so the book churned.

**With limits** — swap only on a breakout's first day, only sell holdings held
20+ trading days, at most one swap a day:

| Version | Total | Max DD | Sharpe | Swaps |
|---|---|---|---|---|
| **No swaps** | **+164%** | −28.5% | **0.63** | 0 |
| Nearest to exit, margin 2 | +60% | −36.0% | 0.36 | 1,200 |
| Worst return, margin 1 | +52% | −42.9% | 0.33 | 1,237 |
| Momentum, margin 1 | +51% | −39.9% | 0.32 | 1,289 |
| Momentum, margin 1, holdings kept 60+ days | +125% | −26.5% | 0.55 | 164 |
| Momentum, margin 1, any breakout day | +61% | −39.0% | 0.35 | 1,237 |
| Momentum, margin 1, no daily limit | +32% | −44.3% | 0.24 | 1,260 |

The remaining combinations returned −3% to +39%. From a 2017 start the best
version made +38% (no swaps: +147%); from 2018, +32% (no swaps: +105%).

**Finding.** The stock bought was no better than the stock sold: over the next
20 trading days it outperformed about half the time, by −0.5% to +0.04% on
average. With no edge, every swap only adds cost — and it cuts winners short.
The win rate rose from 43% to 53% while total return fell, because
trend-following earns its money from a few long runs, and a winner in a
normal pullback looks exactly like the weakest holding. The more a version
protects existing holdings, the closer it gets back to no swaps. Trading costs
rose by only ~$10–15k, so they are a minor part of the gap.

## 6. Volatility filter: stop taking trades when volatility is abnormally high

*Scripts: `vol_filter.py`, `vol_filter_shock.py` · 2026-09-16*

**Rule.** Computed on weekly (Friday) closes from past data only, and applied
to new buys only — exits are unchanged. Three layers, any of which blocks an
entry:

- **Market:** SPY's volatility is high relative to its own normal.
- **Stock:** the stock's volatility is high relative to its own normal.
- **Shock:** a single huge weekly move, for SPY or the stock, because rolling
  volatility takes weeks to catch up to a sudden spike.

Parameters, fixed before any result was seen: volatility is the 13-week
standard deviation of weekly log returns; "normal" is the median of that
volatility over the previous 260 weeks (5 years, at least 104), excluding the
current week; "high" is above 1.5× normal; a shock is a weekly move larger
than 3× normal, and blocks entries that week and the next three. A daily
decision uses the latest week that ended on or before it. The price cache
starts in 2016, so 2010–2015 closes were fetched separately (kept out of the
cache) to give each asset its 5-year history; 162 of 710 tickers have no
pre-2016 data, and their stock layer switches on after 2 years of their own.
Flags computed with data cut off at 2020-06-30 matched the full run, so
nothing leaks from the future.

| Filter | Total | Max DD | Sharpe | Trades | 2020 | 2022 |
|---|---|---|---|---|---|---|
| **No filter** | **+164%** | −28.5% | **0.63** | 449 | +0.1% | −12.9% |
| Market layer only | +51% | −31.1% | 0.32 | 350 | −3.4% | −13.2% |
| Stock layer only | +89% | −27.5% | 0.45 | 427 | −0.3% | −15.3% |
| Shock only | +160% | −24.4% | 0.61 | 399 | +0.1% | −8.0% |
| **All three (the design)** | **+98%** | −29.6% | 0.47 | 334 | −9.5% | −8.8% |
| All three, only down weeks count as shocks | +61% | −32.5% | 0.36 | 346 | −7.0% | −11.3% |
| All three, "high" at 1.25× normal | +116% | −26.6% | 0.54 | 278 | +3.2% | −5.7% |
| All three, "high" at 2× normal | +105% | −29.7% | 0.50 | 370 | +0.1% | −15.4% |
| Also sell everything when SPY volatility is high (diagnostic) | +99% | −24.3% | 0.50 | 342 | −13.9% | −4.5% |

Other start dates:

| Start | No filter | All three | Shock only | Shock only, down weeks |
|---|---|---|---|---|
| 2016 | +164%, −28.5%, 0.63 | +98%, −29.6%, 0.47 | +160%, −24.4%, 0.61 | +129%, −31.8%, 0.54 |
| 2017 | +147%, −28.5%, 0.63 | +74%, −28.3%, 0.42 | +119%, −23.4%, 0.57 | +133%, −28.4%, 0.59 |
| 2018 | +105%, −28.5%, 0.56 | +25%, −27.8%, 0.23 | +81%, −24.2%, 0.49 | +120%, −25.1%, 0.61 |

(Each cell: total return, max drawdown, Sharpe.)

**Finding.** The filter blocks the *better* trades. Of the unfiltered run's
449 trades, the full rule would have blocked 162; those averaged **+3.0%**,
against **+1.7%** for the 287 it let through (similar median and win rate —
the blocked group had bigger winners). Donchian rarely buys during the fall
itself, and volatility stays high for months afterwards — SPY counted as
high-volatility on 37 weeks in 2020 and 40 in 2022, and the market layer
blocks 26% of trading days — so the blocking lands mostly in recoveries, when
breakouts run furthest. Filtering entries also can't cut exposure in a crash
while the book stays ~97% invested; even selling everything on a SPY spike
only improved the drawdown by 4 points, at a cost of 65 points of return.
Results don't move consistently with the threshold (1.25× and 2× both beat
1.5×), which points to noise. **Shock only** was the closest to neutral: it
trimmed the drawdown by 4–5 points from every start, but cost 4, 28 and 23
points of return and some Sharpe each time — a trade-off, not an
improvement. Counting only down weeks as shocks was worse from 2016 and
better from 2018, again inconsistent. 2016–2026 had two sharp crashes that
recovered fast; a long grinding bear market (2000–02, 2008) might favour the
filter more, and this data can't test that.


## 7. Liquidity-sweep reversal — fading the break that fails

*Package: `backend/research/sweep/` · 2026-09-17*

**The premise.** Resting stops cluster just beyond obvious levels. Price reaches
past a level to trigger them, then reverses, because the push was about filling
orders rather than direction. The trade is a fade, never a breakout entry: wait
for a level to break, close back inside, confirm with a market structure shift,
enter on the retrace into the imbalance the shift left, stop beyond the sweep
wick, target the next pool.

**Why it is interesting next to Donchian.** The two systems disagree about the
same bar at the same price. Donchian buys a close *above* the prior 55-day high;
this fades a push above that high which *closes back under* it. The 55-day high
is itself a textbook liquidity pool. So "does the break hold?" is the single
question separating them, and it is answerable on the closing print.

That also reframes what experiments 1-4 found. Donchian's short leg did not lose
because shorting is hopeless — it lost because the *exit* is structurally broken
(experiment 2: a short covers on a close above the 55-day high, so a cover
within 55 sessions is necessarily above the entry). A short with its own stop
and its own target does not inherit that defect, which is why this was worth
building rather than assuming.

### 7a. The daily-bar veto — no shorting, no new data

Run through the real `run_backtest`, unmodified, on the usual universe and
period. Both variants are pure transforms of the signal frame, so the engine,
its costs, its sizing, its membership handling and its look-ahead guard are all
the measured ones:

- **skip** — drop a breakout if that level was swept and rejected within the
  last *N* bars;
- **exit** — sell a holding on the bar that sweeps and rejects.

| Variant | Total | CAGR | Max DD | Sharpe | Trades | Win | PF |
|---|---|---|---|---|---|---|---|
| **No veto (baseline)** | **+164%** | 9.58% | −28.5% | **0.63** | 449 | 43.0% | 1.51 |
| skip, 1-bar window | +164% | 9.58% | −28.5% | 0.63 | 449 | 43.0% | 1.51 |
| skip, 2-bar window | +161% | 9.48% | −27.9% | 0.60 | 432 | 43.5% | 1.48 |
| skip, 3-bar window | +125% | 7.96% | −32.3% | 0.53 | 410 | 43.2% | 1.32 |
| skip, 5-bar window | +145% | 8.84% | −29.3% | 0.57 | 430 | 42.1% | 1.35 |
| skip, 10-bar window | **+181%** | 10.24% | −29.2% | 0.66 | 408 | 42.4% | 1.44 |
| skip, 20-bar window | +84% | 5.92% | −33.6% | 0.43 | 405 | 43.2% | 1.28 |
| exit on the sweep | +125% | 7.94% | −30.4% | 0.55 | **2,306** | 58.4% | 1.21 |

The 1-bar row is a parity check, not a result: a bar cannot both close above the
level and close back under it, so a 1-bar window can veto nothing and must
reproduce the baseline exactly. It does, to the cent.
`test_a_one_bar_veto_window_reproduces_the_baseline_exactly` pins it.

**Was the premise true at all?** Tagging each of the baseline's 449 trades by
whether its level had been swept in the preceding *N* bars:

| Window | Swept before: n / mean / median / win | Clean: n / mean / median / win | p |
|---|---|---|---|
| 3 | 105 · **+1.18%** · −0.77% · 46.7% | 344 · **+2.19%** · −1.90% · 41.9% | — |
| 5 | 162 · **+1.44%** · −1.08% · 45.7% | 287 · **+2.24%** · −1.91% · 41.5% | 0.64 |
| 10 | 254 · **+1.51%** · −1.49% · 43.7% | 195 · **+2.53%** · −1.75% · 42.1% | 0.54 |
| 20 | 317 · **+1.41%** · −1.40% · 42.9% | 132 · **+3.27%** · −2.45% · 43.2% | — |

(p: two-sided permutation test on the difference in means, 20,000 resamples.)

**Finding.** The premise points the right way and the effect is not measurable.
Previously-swept breakouts earned less on average at every window, consistently —
but they also won *more often* and had a better median. The whole difference is
in the right tail: the clean group holds the big winners, which is the same
shape experiment 6 found for the volatility filter, and the same reason.
Trend-following earns from a few long runs, and a rule that raises win rate by
removing tail candidates trades away the thing that pays. At n = 162 against
n = 287 with ~13-19pp standard deviations, p ≈ 0.54-0.64: **indistinguishable
from noise.**

The portfolio results say the same thing more bluntly. Across veto windows the
effect changes sign — one setting better by 17pp, four worse by up to 80pp, with
no monotonicity in between. That is the spike-not-plateau signature the spec
warns about, and reading the +181% row as a result would be curve-fitting to one
window length. The `exit` variant is a separate lesson: it quintuples the trade
count, lifts the win rate from 43% to 58%, and still cuts return to +125%,
because selling into every failed push cuts winners short.

### 7b. The intraday strategy — built, tested, not yet measured

The method proper is intraday: Asia (20:00-00:00 ET) and London (02:00-05:00 ET)
session levels, 4H bias, 1H structure, entries in 5-minute imbalances. **Yahoo
cannot supply it.** Measured 2026-09-17: hourly bars reach back ~2.4 years and
15m/5m only ~70 days, and for US equities Yahoo returns regular hours only
(09:30-16:00 ET), so the Asia and London sessions the premise is built on do not
exist in the data at all. Only 24-hour futures carry them. A Databento 5-minute
feed is coming; the harness and its seam (`sources.DatabentoSource`) are
written and the strategy code is source-agnostic.

What exists and is tested offline:

- all five components, each event carrying a `known_at` stamp;
- a simulator with limit-order fills, an intrabar stop *and* target, shorts,
  borrow, and per-instrument contract specs;
- 35 tests, including a truncation-invariance test that re-runs on a prefix of
  the data and requires every earlier setup and closed trade to come out
  identical.

Four conventions are resolved **against** the strategy and are worth knowing
before reading any number it eventually produces: a limit fills only on a trade
*through* it, never a touch; a bar covering both stop and target resolves to the
stop; a position is checked for its stop on its own entry bar; and a gap fills
at the open.

Two traps found while building it, both of which would have decided the result
on their own:

- **Notional basis points are the wrong cost model for futures.** One ES
  contract at 4,950 controls $247,500 and costs ~$27.50 round-turn, not the $495
  that 10bps of notional implies. Charged the wrong way, a 5-minute strategy
  with a two-point stop pays ~60R per trade in fees and every result afterwards
  is a report on the cost model. `instruments.py` exists for this.
- **Sharpe must be computed on daily equity.** `app/backtest/metrics.py`
  annualises with sqrt(252). Handing it a 5-minute curve inflates volatility by
  about sqrt(78) and divides Sharpe by the same — a number that looks like a
  finding and is an artefact of the bar size.

A daily-bar configuration exists (`sources.daily_params`) and runs, but it is
for smoke-testing only: a daily bar contains no sessions, and collapsing the
bias, structure and execution timeframes onto one series removes the
higher-timeframe separation the method depends on. The CLI prints that caveat
above any number it produces in that mode.

---

## What the experiments add up to

- **Shorting after Donchian breakdowns lost money everywhere it was tried** —
  individual stocks and SPY, with slow or fast covers, with or without costs.
  In 2016–2026 a stock breaking below its 20-day low was about as likely to
  bounce as to keep falling. A short strategy worth testing would need a
  different entry, not only a different exit.
- **Being fully invested isn't a constraint worth engineering around.** The
  breakouts skipped for cash are about as good as the positions already held.
- **Filtering entries on high volatility gives up the rebounds.** The trades
  it blocks were better than the ones it lets through; at best (shocks only)
  it swaps some return for a slightly shallower drawdown.
- **Fading failed breakouts points the right way and does not measure.**
  Breakouts at recently-swept levels did earn less, at every window tested — but
  they also won more often, the gap is entirely in the right tail, and it is not
  statistically separable from noise (p ≈ 0.54-0.64). Across veto windows the
  effect changes sign, which is the signature of fitting rather than of an edge.
- **Two of these findings now rhyme.** The volatility filter (6) and the sweep
  veto (7a) both raise win rate, both cut total return, and both do it by
  removing candidates that were more likely to produce the few long runs the
  system earns from. Any future rule that filters Donchian entries should be
  judged on the tail it removes, not on the win rate it buys.
- **The long-only system remains the best of these variants — and it still
  trails SPY buy & hold** (+164% vs +353%).
