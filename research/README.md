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

### 7b. The intraday strategy on NQ — measured, 2026-09-24

*Data: Databento GLBX.MDP3 `ohlcv-1m`, `NQ.v.0`/`NQ.v.1` (volume roll),
2022-01-02 to 2026-08-31, additive back-adjustment across 18 quarterly rolls.
Runner: `python -m research.sweep.futures_eval --symbols NQ`.*

**Data.** 1,650,684 one-minute bars, resampled to 330,210 five-minute bars;
1H, 4H and daily are resampled from those, never fetched. All 18 rolls were
priced the preferred way — the two contracts' closes at the same minute — with
no fallbacks and no outsized gaps: from −3 points in March 2022 (rates near
zero) up to 200-316 points as rates and price rose, which is what quarterly
carry should look like. ES is not yet included (see below).

**Costs and sizing.** $1.25 commission plus one tick ($5) of slippage per side
per contract, on entries as well as exits, although a limit entry would
normally not slip. Sized at 1% of equity per trade on the stop distance, with
the stock book's equity-fraction caps removed (a futures contract is margined,
not paid for in full); fractional contracts stand in for Micro NQ. Every
setup's realised risk was exactly 1.00%.

| Run | Trades | Win | Mean R | Total R | PF | Max DD (R) | Return @1% | Max DD @1% |
|---|---|---|---|---|---|---|---|---|
| **Default rules (4H bias on)** | 147 | 26.5% | **+0.035** | +5.1 | 1.05 | −22.5 | +2.6% | −20.7% |
| No bias filter | 283 | 23.3% | −0.105 | −29.8 | 0.87 | −37.9 | −28.5% | −33.5% |
| Default, zero costs | 147 | 26.5% | +0.061 | +9.0 | 1.09 | −20.9 | +6.7% | −19.4% |
| Default, double costs | 147 | 26.5% | +0.008 | +1.2 | 1.01 | −24.2 | −1.4% | −22.0% |
| Long only (swept a low) | 61 | 31.1% | +0.247 | +15.1 | 1.36 | −7.2 | | |
| Short only (swept a high) | 86 | 23.3% | −0.116 | −10.0 | 0.85 | −21.6 | | |

Bootstrap 95% interval on the default's mean R: **[−0.258, +0.347]**, with 42%
of resamples at or below zero. By year: +7.2R, −10.1R, −12.5R, +15.1R, +5.4R
(2022-2026).

**Finding.** The strategy as specified has no measurable edge on NQ: +0.035R
per trade, which is zero within any reasonable error and slightly negative once
costs double. The 4H bias filter is the only component that clearly earns its
place, since removing it takes the result from breakeven to −0.105R. **On the
question that started this, shorts:** the short side lost (−0.116R), and the
long side's +0.25R across 61 trades is well within noise over a period in
which NQ roughly doubled. It is no better a short signal than Donchian's.

Funnel, which shows where the rules bite: 4,536 sweeps → 2,204 rejected by
bias → 1,789 with no structure shift inside the window → 70 with no gap, 149
under 1.5R, 63 with no target → 253 setups → 147 filled (73 skipped because a
position was already open, 33 expired unfilled).

#### A look-ahead bug, found by the sensitivity grid

The first NQ run reported +0.163R per trade and +25R. It was wrong. A setup
went live when its fair value gap completed, but the gap search can return a
gap that formed **before** the structure shift — and 25% of setups did. Their
limit orders were resting before the confirmation existed, and filled on
pullbacks that had, in effect, been selected for being followed by a break of
structure.

What exposed it was the spec's rule to treat a suspiciously strong result as a
bug first. Widening `sweep_window` from 12 to 48 bars took the result from +25R
to **+248R** — a looser confirmation rule earning ten times more, which the
premise gives no reason for. The damage scaled with the window because a wider
window lets the order go live earlier.

Fixed: a setup now goes live at the close of the bar after the shift, because
that bar decides which gap is used. The default fell from +0.163R to +0.035R —
**the whole of the apparent edge was the leak.** Two lessons, both now in the
tests:

- **Random-cut truncation tests cannot catch a short-span leak.** This one
  lived for a few bars around each setup; the existing truncation test passed
  on the buggy code at every cut point tried.
  `test_every_setup_is_reproducible_from_the_data_at_its_go_live` cuts the
  data at each setup's own go-live instant instead, and fails on the old code.
- **Truncation has to be checked in both directions.** A leak shows up as a
  setup the full run has and the prefix lacks. The original test checked only
  the other direction.

The fixed code was then checked on real data: 14 NQ setups spread across the
whole period, at `sweep_window` 48, each rebuilt exactly from only the bars
closed by its go-live time.

#### The one region that is positive — a hypothesis, not a result

After the fix, a wider structure-shift window is still positive, and forms a
plateau rather than a spike:

| `sweep_window` (5-min bars) | Trades | Win | Mean R | Total R | Max DD @1% | 95% CI on mean R |
|---|---|---|---|---|---|---|
| 12 (1 hour, the default) | 147 | 26.5% | +0.035 | +5.1 | −20.7% | [−0.26, +0.35] |
| **24 (2 hours)** | 325 | 23.7% | **+0.276** | +89.6 | −34.0% | [−0.11, +0.77] |
| 48 (4 hours) | 431 | 23.0% | +0.229 | +98.6 | −32.0% | [−0.11, +0.64] |
| 96 (8 hours) | 459 | 22.2% | +0.190 | +87.2 | −36.1% | — |

It is not established, for three reasons:

- **The interval still includes zero** (about 10% of resamples at or below
  it), and that is before discounting for having searched roughly 35 settings
  to find it.
- **It is two years, not five.** At window 24: 2022 +56.9R, 2023 −11.5R,
  2024 −24.6R, 2025 +69.8R, 2026 −1.0R. The winning years are NQ's two most
  violent — the 2022 bear market and the April 2025 tariff crash. That reads
  as a volatility-regime effect, not a steady edge.
- **The drawdowns are about a third of the account** at 1% risk, with losing
  runs of 30-40R.

The rest of the grid is flat around the default: `min_body_frac`,
`min_fvg_ticks`, `min_r`, `fvg_entry_depth`, `entry_expiry_bars` and
`level_max_age_days` all land between roughly −6R and +14R. `pivot_lookback`
is a spike (+5R at 3, −25R at 2, −11R at 8).

**Next test, fixed in advance.** ES is untouched data. The hypothesis to test
there, with no further tuning, is **`sweep_window = 24`, every other parameter
at its default**. If it holds on ES, it is worth pursuing; if it does not, the
NQ result was the search finding noise. The ES pull stopped on a Databento
billing limit (`402 account_insufficient_funds`) after its 2022 chunk. The
cache makes the rerun cost only the missing ES chunks, roughly $8.

#### Conventions a result from this harness depends on

A limit entry fills only on a trade *through* the price, never a touch. A bar
covering both stop and target resolves to the stop. On the bar a limit fills,
the stop can trigger but the target cannot: whether the high came before the
dip is unknowable, so that win is never credited. The stop half of that is
exact rather than pessimistic, because for a long, a low below the stop passed
through the higher limit first. Gaps fill at the open. And Sharpe comes from a
daily equity curve, because `metrics.py` annualises with √252.

## 8. Three short strategies built on the Donchian levels

*Package: `backend/research/donchian_shorts/` · runner:
`python -m research.donchian_shorts.evaluate` · 2026-09-25*

Three short strategies meant to complement the 55-day-high / 20-day-low long
system, each written up in prose and turned into rules here:

- **S1 Macro 20-day breakdown** (trend-following). Short at the next open after
  a close crosses below the prior 20-day low, provided there was no new 55-day
  high in the last 20 sessions. Stop at the 55-day high, checked intraday and
  trailing down daily. No target.
- **S2 55-day fakeout / Turtle Soup** (counter-trend). Price trades above the
  prior 55-day high, then closes back below that same line within 3 days (the
  breakout day counts as day 0). Stop 1¢ above the breakout's peak; target the
  20-day low.
- **S3 Pullback short inside the channel.** After a 20-day-low breakdown, with
  price at least 3 ATR below the 55-day high: a rally on lighter volume back to
  within ¼ ATR of the broken line, then a bearish engulfing or shooting-star
  candle that closes below it. Stop 1¢ above the rally high; target the low the
  rally started from. The prose gives no target, so that one is a choice.

Every other place the prose needed interpreting is listed in `rules.py`, and
the sensitivity table below moves each one.

**Method.** The same universe, period, costs and borrow as experiments 1-6. The
engine reproduces `run_backtest` for the longs — checked on the real universe
before anything is printed, and it matched to $0.0000 — and adds shorts to the
same account. Each short is sized at 1% risk on its actual stop distance and
reserves its full notional as collateral (no margin). Stops and targets are
checked intraday, including on the entry bar, since the fill is that bar's
open. A bar reaching both resolves to the stop; an open through either fills
at the open. When a Donchian long and a short compete for the same slot on the
same day, the long is funded first.

| Run | Total | CAGR | Max DD | Sharpe | Shorts | Short win | Short P&L | Long P&L |
|---|---|---|---|---|---|---|---|---|
| **Donchian long only** | **+164%** | 9.6% | −28.5% | **0.63** | — | — | — | $163k |
| SPY buy & hold | +355% | 15.4% | −33.7% | 0.89 | | | | |
| S1 alone | −70% | −10.6% | −69.9% | −0.72 | 698 | 27% | −$70k | — |
| S1 in one book with the longs | +47% | 3.7% | −35.5% | 0.32 | 136 | 22% | −$46k | $92k |
| S1 as a 50/50 sleeve | +47% | 3.7% | **−23.0%** | 0.36 | | | | |
| S2 alone | −97% | −28.3% | −97.1% | −1.75 | 5,009 | 6% | −$97k | — |
| S2 in one book with the longs | +140% | 8.6% | −32.4% | 0.58 | 85 | 27% | +$1k | $139k |
| S2 as a 50/50 sleeve | +33% | 2.7% | −30.3% | 0.26 | | | | |
| S3 alone | −88% | −18.3% | −88.3% | −2.03 | 2,448 | 38% | −$88k | — |
| S3 in one book with the longs | +126% | 8.0% | −31.8% | 0.54 | 116 | 30% | −$21k | $147k |
| S3 as a 50/50 sleeve | +38% | 3.1% | −27.4% | 0.29 | | | | |

Short book alone, by year, beside Donchian:

| Year | Donchian | S1 | S2 | S3 |
|---|---|---|---|---|
| 2016 | +14.4% | −9.6% | −21.7% | −18.0% |
| 2017 | +21.1% | +0.4% | −42.8% | −13.5% |
| **2018** | **−19.5%** | **+5.3%** | −6.2% | −18.0% |
| 2019 | +29.1% | −22.8% | −48.0% | −14.1% |
| 2020 | +0.1% | −18.3% | −17.7% | −14.6% |
| 2021 | +51.3% | −33.0% | −36.7% | −15.2% |
| **2022** | **−12.9%** | **+9.9%** | −27.7% | −28.0% |
| 2023 | +3.2% | −6.3% | −24.3% | −14.7% |
| 2024 | +26.2% | −19.7% | −33.7% | −5.7% |
| 2025 | +4.7% | −6.5% | −20.9% | −29.4% |
| 2026 | +2.0% | −2.7% | −9.4% | −20.7% |

Sensitivity — each judgement call moved, the rest at default:

| Rule | Setting | Alone | Sharpe | Shorts | One book | Sharpe |
|---|---|---|---|---|---|---|
| S1 | default (stall 20) | −70% | −0.72 | 698 | +47% | 0.32 |
| S1 | no stall filter | −68% | −0.69 | 673 | +12% | 0.15 |
| S1 | stall 10 | −72% | −0.77 | 680 | +51% | 0.33 |
| S1 | stall 40 | −76% | −0.82 | 724 | +61% | 0.37 |
| S2 | default (3-day window) | −97% | −1.75 | 5,009 | +140% | 0.58 |
| S2 | same-day failures only | −99% | −2.08 | 5,476 | +159% | 0.63 |
| S2 | 1-day window | −98% | −2.01 | 5,153 | +151% | 0.61 |
| S2 | 5-day window | −98% | −1.83 | 4,788 | +101% | 0.49 |
| S2 | stop ¼ ATR above the peak | −95% | −1.68 | 3,461 | +104% | 0.50 |
| S2 | stop ½ ATR above the peak | −88% | −1.20 | 2,178 | +111% | 0.49 |
| S3 | default (3 ATR, ¼ ATR touch, volume on) | −88% | −2.03 | 2,448 | +126% | 0.54 |
| S3 | 2 ATR below the high | −88% | −2.03 | 2,474 | +96% | 0.46 |
| S3 | 5 ATR below the high | −80% | −1.93 | 1,699 | +133% | 0.55 |
| S3 | touch within 0.1 ATR | −83% | −1.90 | 2,110 | +148% | 0.59 |
| S3 | touch within 0.5 ATR | −92% | −2.14 | 2,843 | +136% | 0.56 |
| S3 | no volume filter | −96% | −1.94 | 3,991 | +163% | 0.62 |

**Finding.** None of the three works, as a strategy or as a complement.

- **All three lost on their own, in every variant** (−68% to −99%). No
  setting of any judgement call came close to breakeven.
- **Adding any of them to the Donchian book lowered its return.** Across all
  16 one-book variants the best were +163% and +159%, against +164% for the
  longs alone — never better, only at best neutral. The shorts also crowd the
  longs out of capital: long profit fell from $163k to $92k with S1.
- **The negative correlation is the market, not a hedge.** S1 and S2's
  monthly returns run at −0.55 and −0.61 against Donchian, but that is what
  any short book does in a rising market, and it only helps if the stream
  makes money. S1 did make money in 2018 and 2022, the two years Donchian
  lost — +5% and +10% — then lost 20-33% in the bull years. As a sleeve it cut
  the drawdown from −28.5% to −23% at a cost of 117 points of return and half
  the Sharpe.
- **S2's failure has a specific shape.** A stop a cent above the breakout
  peak is usually hit the same day — 61% of fakeout shorts were — because
  S&P breakouts that close back under the line tend to retest the high
  rather than reverse. That matches experiment 7a from the other side:
  breakouts at recently-swept levels won *more* often as longs. The average
  R (−27) is an artefact: when the next open lands just under the stop, the
  risk is tiny (10% of trades risked under 0.11% of price, less than the
  round-trip cost), so the loss becomes a huge multiple. The median trade is
  −1.35R.

**Caveat.** 2016-2026 is one long bull market (SPY +355%) with two sharp,
fast-recovering declines. A long grinding bear market (2000-02, 2008) might
treat S1 better, since it was the only rule to make money in both down years.
The price cache starts in 2016, so this data cannot test that.

## What the experiments add up to

- **Seven short rules have now been tested against this system and all
  lost** — breakdown shorts (1-4, 8 S1), fakeouts at the 55-day high (8 S2),
  pullback shorts (8 S3) and the intraday sweep reversal on NQ (7b). The
  pattern is consistent across all of them: in 2016-2026, a stock that fails
  at a high or breaks a low bounced about as often as it followed through.
  Negative correlation with the long book is not enough to justify a short
  leg that loses money on average.
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
- **The codified TJR/ICT sweep reversal has no edge on NQ as specified**
  (+0.035R per trade over 147 trades, 2022-2026), and its short side lost. The
  one positive region — a wider structure-shift window — is a lumpy,
  two-good-years hypothesis that needs ES, untouched, before it means anything.
- **A strategy made of chart features is a look-ahead trap.** The first NQ run
  showed +25R, and all of it was a short-span leak that a random-cut
  truncation test passed straight over. Cut at each setup's own go-live
  instead; `test_every_setup_is_reproducible_from_the_data_at_its_go_live`.
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
