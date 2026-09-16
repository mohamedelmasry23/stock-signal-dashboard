# Research experiments

Strategy ideas tested against the Donchian system **as research only**. None
of them is implemented in the app, and none was adopted: every one did worse
than the long-only system it was compared with.

The scripts were one-off files kept outside the repository, so they are not
committed. This file records what each one did and what it found, so the
experiments don't need to be re-run to know the answer.

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

---

## What the experiments add up to

- **Shorting after Donchian breakdowns lost money everywhere it was tried** —
  individual stocks and SPY, with slow or fast covers, with or without costs.
  In 2016–2026 a stock breaking below its 20-day low was about as likely to
  bounce as to keep falling. A short strategy worth testing would need a
  different entry, not only a different exit.
- **Being fully invested isn't a constraint worth engineering around.** The
  breakouts skipped for cash are about as good as the positions already held.
- **The long-only system remains the best of these variants — and it still
  trails SPY buy & hold** (+164% vs +353%).
