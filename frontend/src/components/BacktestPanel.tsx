import { AlertTriangle, FlaskConical } from 'lucide-react'
import type { Backtest, BacktestRun, BacktestSummary } from '../lib/api'
import { EquityChart } from './EquityChart'
import { Badge, Card, CardHeader, EmptyState, Skeleton, cn } from './ui'

function num(summary: BacktestSummary | null | undefined, key: string): number | null {
  const value = summary?.[key]
  return typeof value === 'number' ? value : null
}

function pct(value: number | null, digits = 1): string {
  return value === null ? '—' : `${(value * 100).toFixed(digits)}%`
}

function fixed(value: number | null, digits = 2): string {
  return value === null ? '—' : value.toFixed(digits)
}

/** Green when the strategy beat the benchmark, red when it didn't. Same
 *  tokens as the holdings rows, so the colour means one thing across the
 *  whole dashboard.
 *
 *  `higherIsBetter` is false for metrics like volatility where a smaller
 *  number is the better outcome. Note max drawdown is higherIsBetter:
 *  both values are negative, so -32% beating -34% is a greater-than. */
function verdictClass(strategy: number | null, benchmark: number | null, higherIsBetter: boolean) {
  if (strategy === null || benchmark === null) return ''
  const better = higherIsBetter ? strategy > benchmark : strategy < benchmark
  return better ? 'text-positive' : 'text-destructive'
}

function StatTile({
  label,
  value,
  sub,
  tone,
  title,
}: {
  label: string
  value: string
  sub?: string
  tone?: string
  title?: string
}) {
  return (
    <div className="min-w-0" title={title}>
      <p className="text-[11px] text-muted-foreground mb-0.5">{label}</p>
      <p className={cn('text-xl font-semibold tracking-tight tnum', tone)}>{value}</p>
      {sub && <p className="text-[11px] text-muted-foreground mt-0.5 tnum">{sub}</p>}
    </div>
  )
}

function ComparisonTable({ run }: { run: BacktestRun }) {
  const s = run.summary
  const b = run.benchmark_summary

  const rows: [string, string, number | null, number | null, boolean, string][] = [
    ['Total return', 'total_return', num(s, 'total_return'), num(b, 'total_return'), true, 'pct'],
    ['CAGR', 'cagr', num(s, 'cagr'), num(b, 'cagr'), true, 'pct'],
    ['Max drawdown', 'max_drawdown', num(s, 'max_drawdown'), num(b, 'max_drawdown'), true, 'pct'],
    ['Volatility', 'volatility', num(s, 'volatility'), num(b, 'volatility'), false, 'pct'],
    ['Sharpe', 'sharpe', num(s, 'sharpe'), num(b, 'sharpe'), true, 'num'],
    ['Sortino', 'sortino', num(s, 'sortino'), num(b, 'sortino'), true, 'num'],
  ]

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs tnum">
        <thead>
          <tr className="text-muted-foreground text-[11px]">
            <th className="text-left font-normal py-1.5">Metric</th>
            <th className="text-right font-normal py-1.5">Strategy</th>
            <th className="text-right font-normal py-1.5">SPY buy &amp; hold</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, key, strat, bench, higherIsBetter, fmt]) => (
            <tr key={key} className="border-t border-border">
              <td className="py-1.5 text-muted-foreground">{label}</td>
              <td
                className={cn(
                  'py-1.5 text-right font-medium',
                  // Volatility isn't better or worse on its own — it's
                  // context for the return, so it gets no verdict colour.
                  key !== 'volatility' && verdictClass(strat, bench, higherIsBetter),
                )}
              >
                {fmt === 'pct' ? pct(strat) : fixed(strat)}
              </td>
              <td className="py-1.5 text-right text-muted-foreground">
                {fmt === 'pct' ? pct(bench) : fixed(bench)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function VariantComparison({ faithful, stopped }: { faithful: BacktestRun; stopped: BacktestRun }) {
  const rows: [string, string, string, string][] = [
    ['Total return', pct(num(faithful.summary, 'total_return')), pct(num(stopped.summary, 'total_return')), 'pct'],
    ['Max drawdown', pct(num(faithful.summary, 'max_drawdown')), pct(num(stopped.summary, 'max_drawdown')), 'pct'],
    ['Sharpe', fixed(num(faithful.summary, 'sharpe')), fixed(num(stopped.summary, 'sharpe')), 'num'],
    [
      'Losses worse than 1R',
      pct(num(faithful.summary, 'pct_losses_worse_than_1r')),
      pct(num(stopped.summary, 'pct_losses_worse_than_1r')),
      'pct',
    ],
    ['Trades', String(num(faithful.summary, 'n_trades') ?? '—'), String(num(stopped.summary, 'n_trades') ?? '—'), 'int'],
  ]

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs tnum">
        <thead>
          <tr className="text-muted-foreground text-[11px]">
            <th className="text-left font-normal py-1.5">Metric</th>
            <th className="text-right font-normal py-1.5">As built (no stop)</th>
            <th className="text-right font-normal py-1.5">With 2×ATR stop</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, a, b]) => (
            <tr key={label} className="border-t border-border">
              <td className="py-1.5 text-muted-foreground">{label}</td>
              <td className="py-1.5 text-right font-medium">{a}</td>
              <td className="py-1.5 text-right font-medium">{b}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The snapshot year, read from the universe label the backtest CLI
 *  stored (`sp500_2016,etfs`), falling back to the run's own start. */
function snapshotYear(run: BacktestRun): string {
  return run.universe.match(/sp500_(\d{4})/)?.[1] ?? run.start_date.slice(0, 4)
}

function Figure({ children }: { children: string }) {
  return <span className="text-foreground font-medium tnum">{children}</span>
}

/**
 * What the chart above is actually saying, in prose.
 *
 * Figures are read from the runs rather than written into the text: a
 * caption quoting a number the chart no longer shows is worse than no
 * caption, and these are regenerated every time a backtest is re-run.
 */
function ChartNote({ run, pointInTime }: { run: BacktestRun; pointInTime: BacktestRun | null }) {
  const strategy = pct(num(run.summary, 'total_return'), 0)
  const benchmark = pct(num(run.benchmark_summary, 'total_return'), 0)

  return (
    <div className="mt-3 rounded-lg border border-border bg-muted/40 px-4 py-3">
      <p className="text-xs font-medium">Reading this chart</p>
      <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
        Every line starts at 100 — $100 invested on the first day, compounded by that series' own
        returns — so their shapes stay comparable even though the runs differ.{' '}
        {pointInTime ? (
          <>
            The solid and dashed lines are the <em>same</em> strategy over two different universes:
            today's index membership (<Figure>{strategy}</Figure>) and the S&amp;P 500 frozen as it
            stood in {snapshotYear(pointInTime)} (
            <Figure>{pct(num(pointInTime.summary, 'total_return'), 0)}</Figure>). Today's list omits
            every company that failed along the way, and the {snapshotYear(pointInTime)} list omits
            everything added since — so the pair brackets the truth rather than pinning it down, and
            buy &amp; hold's <Figure>{benchmark}</Figure> sits inside that bracket.
          </>
        ) : (
          <>
            The strategy ended at <Figure>{strategy}</Figure> against <Figure>{benchmark}</Figure>{' '}
            for buy &amp; hold.{' '}
            {run.strategy === 'rsi' ? (
              <>
                Read those side by side only with the band underneath in view: this strategy was
                invested an average of{' '}
                <Figure>{pct(num(run.summary, 'avg_gross_exposure'), 0)}</Figure> of the account, so
                it is not competing for the same money. The fairer comparisons are risk-adjusted —
                Sharpe <Figure>{fixed(num(run.summary, 'sharpe'))}</Figure> against{' '}
                <Figure>{fixed(num(run.benchmark_summary, 'sharpe'))}</Figure>, and a worst
                drawdown of <Figure>{pct(num(run.summary, 'max_drawdown'))}</Figure> against{' '}
                <Figure>{pct(num(run.benchmark_summary, 'max_drawdown'))}</Figure>. A flat stretch
                in the line with an empty band beneath it is cash, not a losing position.
              </>
            ) : (
              <>
                Steepness is return and depth is drawdown — the long flat-to-falling stretches are
                what holding this system through a bad year actually feels like.
              </>
            )}
          </>
        )}
      </p>
    </div>
  )
}

export function BacktestPanel({
  backtest,
  loading,
  strategy = 'donchian',
}: {
  backtest: Backtest | null
  loading: boolean
  /** Only needed for the empty state, which has to name the command that
   *  would populate it — every other string reads the strategy off the run
   *  itself, which is authoritative once one exists. */
  strategy?: 'donchian' | 'rsi'
}) {
  if (loading) {
    return (
      <Card className="mt-6">
        <CardHeader title="Backtest" />
        <div className="px-5 py-4 space-y-3">
          <Skeleton className="h-6 w-56" />
          <Skeleton className="h-40 w-full" />
        </div>
      </Card>
    )
  }

  const run = backtest?.faithful ?? backtest?.atr_stop ?? null
  if (!run) {
    return (
      <Card className="mt-6">
        <CardHeader title="Backtest" subtitle="Has this strategy ever actually worked?" />
        <EmptyState
          icon={<FlaskConical size={28} strokeWidth={1.5} />}
          title="No backtest has been run"
          hint={
            <>
              Run{' '}
              <code className="font-mono">
                python3 -m app.backtest --strategy {strategy} --universe sp500,etfs --save
              </code>{' '}
              from the backend folder to simulate this strategy over historical prices and populate
              this panel.
            </>
          }
        />
      </Card>
    )
  }

  // The point-in-time curve is only worth drawing beside a run it differs
  // from in exactly one way — the universe. Against the ATR-stop variant,
  // or against a commodities run, two things would have changed at once
  // and the gap between the lines would no longer mean survivorship bias.
  const pointInTime =
    run === backtest?.faithful && run.universe.includes('sp500')
      ? backtest?.point_in_time ?? null
      : null

  const s = run.summary
  const totalReturn = num(s, 'total_return')
  const benchReturn = num(run.benchmark_summary, 'total_return')
  const beat = totalReturn !== null && benchReturn !== null && totalReturn > benchReturn

  return (
    <Card className="mt-6">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <FlaskConical size={14} />
            Backtest
          </span>
        }
        subtitle={`${run.universe} · ${run.start_date} to ${run.end_date}`}
        action={
          <Badge tone={beat ? 'entry' : 'muted'}>
            {beat ? 'Beat SPY' : 'Trailed SPY'}
          </Badge>
        }
      />

      <div className="px-5 py-4">
        <div className="flex flex-wrap gap-x-8 gap-y-4 mb-5">
          <StatTile
            label="Total return"
            value={pct(totalReturn)}
            sub={`SPY ${pct(benchReturn)}`}
            tone={verdictClass(totalReturn, benchReturn, true)}
          />
          <StatTile
            label="CAGR"
            value={pct(num(s, 'cagr'))}
            sub={`SPY ${pct(num(run.benchmark_summary, 'cagr'))}`}
          />
          <StatTile
            label="Max drawdown"
            value={pct(num(s, 'max_drawdown'))}
            sub={`${num(s, 'longest_underwater_days') ?? '—'} days underwater`}
            title="The worst peak-to-trough fall, and how long it took to get back. The second number is what actually makes people abandon a system."
          />
          <StatTile label="Sharpe" value={fixed(num(s, 'sharpe'))} sub={`SPY ${fixed(num(run.benchmark_summary, 'sharpe'))}`} />
          <StatTile
            label="Win rate"
            value={pct(num(s, 'win_rate'))}
            sub={`${num(s, 'n_trades') ?? 0} trades · PF ${fixed(num(s, 'profit_factor'))}`}
            title="Trend systems win less than half the time by design — the profit factor is what matters, not the win rate."
          />
          <StatTile
            label="Time in market"
            value={pct(num(s, 'time_in_market'))}
            sub={`avg exposure ${pct(num(s, 'avg_gross_exposure'))}`}
          />
        </div>

        <EquityChart
          points={run.equity}
          strategyLabel={pointInTime ? "Strategy — today's members" : 'Strategy'}
          // Only where it earns its space. Donchian sits ~98% invested, so
          // the strip would be a solid block saying nothing; RSI is under a
          // fifth invested, where it's the difference between reading the
          // curve correctly and not.
          showExposure={run.strategy === 'rsi'}
          overlay={
            pointInTime
              ? { label: `Strategy — ${snapshotYear(pointInTime)} membership`, points: pointInTime.equity }
              : null
          }
        />
        <ChartNote run={run} pointInTime={pointInTime} />

        <div className="grid gap-6 lg:grid-cols-2 mt-6">
          <div>
            <h3 className="text-xs font-semibold mb-1">vs buy &amp; hold</h3>
            <ComparisonTable run={run} />
          </div>
          {backtest?.faithful && backtest?.atr_stop && (
            <div>
              <h3 className="text-xs font-semibold mb-1">What the missing stop-loss costs</h3>
              <VariantComparison faithful={backtest.faithful} stopped={backtest.atr_stop} />
            </div>
          )}
        </div>

        <div className="mt-5 rounded-lg border border-destructive/30 bg-destructive/[0.06] px-4 py-3">
          <p className="flex items-center gap-2 text-xs font-medium">
            <AlertTriangle size={13} className="text-destructive" />
            Risk model reality check
          </p>
          <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
            <span className="text-foreground font-medium tnum">
              {pct(num(s, 'pct_losses_worse_than_1r'))}
            </span>{' '}
            of losing trades lost more than the {pct(num(s, 'risk_fraction') ?? 0.01, 0)} they were
            sized to risk, the worst losing{' '}
            <span className="text-foreground font-medium tnum">
              {fixed(num(s, 'worst_loss_r'))}×
            </span>{' '}
            its intended risk. Position sizing assumes a stop the strategy doesn't place — the only
            exit is {run.strategy === 'rsi' ? 'the RSI bounce' : 'the Donchian breakdown'}, so a gap
            through it costs whatever it costs.
          </p>
        </div>

        {backtest && backtest.caveats.length > 0 && (
          <ul className="mt-4 space-y-1 text-[11px] text-muted-foreground leading-relaxed">
            {backtest.caveats.map((caveat) => (
              <li key={caveat}>· {caveat}</li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  )
}
