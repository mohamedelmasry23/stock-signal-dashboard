import { AlertTriangle, FlaskConical } from 'lucide-react'
import type { Backtest, BacktestRun, BacktestSummary } from '../lib/api'
import { EquityChart, type Overlay } from './EquityChart'
import { Badge, Card, CardHeader, EmptyState, Skeleton, StatTile, cn } from './ui'

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

/** One column of the variant table: the same strategy under a different
 *  rule, labelled by what that rule is. */
interface Variant {
  label: string
  run: BacktestRun
}

/**
 * The same metrics for each variant, side by side.
 *
 * Takes a list rather than two named props so that a third rule — a
 * different exit, a different fill — is a data change here rather than
 * another rewrite of this table. Note it does NOT generalise the heading
 * above the call site, which is a two-variant sentence; adding a variant
 * still has to say what the comparison is now about.
 */
function VariantComparison({ variants }: { variants: Variant[] }) {
  // Each row reads its own cell out of a run, so adding a column can't
  // leave a row half-filled the way parallel value arrays could.
  const rows: [string, (run: BacktestRun) => string][] = [
    ['Total return', (r) => pct(num(r.summary, 'total_return'))],
    ['Max drawdown', (r) => pct(num(r.summary, 'max_drawdown'))],
    ['Sharpe', (r) => fixed(num(r.summary, 'sharpe'))],
    ['Losses worse than 1R', (r) => pct(num(r.summary, 'pct_losses_worse_than_1r'))],
    ['Trades', (r) => String(num(r.summary, 'n_trades') ?? '—')],
  ]

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs tnum">
        <thead>
          <tr className="text-muted-foreground text-[11px]">
            <th className="text-left font-normal py-1.5">Metric</th>
            {variants.map((v) => (
              <th key={v.label} className="text-right font-normal py-1.5">
                {v.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, cell]) => (
            <tr key={label} className="border-t border-border">
              <td className="py-1.5 text-muted-foreground">{label}</td>
              {variants.map((v) => (
                <td key={v.label} className="py-1.5 text-right font-medium">
                  {cell(v.run)}
                </td>
              ))}
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
function ChartNote({
  run,
  pointInTime,
  currentList,
}: {
  run: BacktestRun
  pointInTime: BacktestRun | null
  currentList: BacktestRun | null
}) {
  const strategy = pct(num(run.summary, 'total_return'), 0)
  const benchmark = pct(num(run.benchmark_summary, 'total_return'), 0)

  return (
    <div className="mt-3 rounded-lg border border-border bg-muted/40 px-4 py-3">
      <p className="text-xs font-medium">Reading this chart</p>
      <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
        Every line starts at 100 — $100 invested on the first day, compounded by that series' own
        returns — so their shapes stay comparable even though the runs differ.{' '}
        {currentList ? (
          <>
            The gold lines are all the <em>same</em> strategy; the only difference is which companies
            it was allowed to buy. The bold line — the one the figures above describe — trades each
            company only while it was actually in the S&amp;P 500, the only one of them a person
            could really have traded. It ended at <Figure>{strategy}</Figure> against buy &amp;
            hold's <Figure>{benchmark}</Figure>. The dashed line uses today's list for the whole
            decade (<Figure>{pct(num(currentList.summary, 'total_return'), 0)}</Figure>). S&amp;P
            adds companies after they have already soared, so that line could buy future members
            before they joined — most of its gain comes from that head start, which is why it runs
            away late on.{' '}
            {pointInTime && (
              <>
                The dotted line freezes the list as it stood in {snapshotYear(pointInTime)} (
                <Figure>{pct(num(pointInTime.summary, 'total_return'), 0)}</Figure>), so it kept
                trading companies after S&amp;P dropped them
                {pointInTime.start_date < '2021-01-01'
                  ? " — the spike in early 2021 is GameStop's squeeze, years after it left the index"
                  : ''}
                .{' '}
              </>
            )}
            Even the bold line leans optimistic: free price data is missing for many companies that
            were later removed, and those were disproportionately the failures.
          </>
        ) : pointInTime ? (
          <>
            The solid and dashed lines are the <em>same</em> strategy over two different universes:
            today's index membership (<Figure>{strategy}</Figure>) and the S&amp;P 500 frozen as it
            stood in {snapshotYear(pointInTime)} (
            <Figure>{pct(num(pointInTime.summary, 'total_return'), 0)}</Figure>). Neither is the list
            a person could have traded at the time — today's could buy future members before they
            joined, the {snapshotYear(pointInTime)} one kept companies after they left — so the gap
            shows how much the choice of list matters, not where the truth lies. Buy &amp; hold
            returned <Figure>{benchmark}</Figure>.
          </>
        ) : (
          <>
            The strategy ended at <Figure>{strategy}</Figure> against <Figure>{benchmark}</Figure>{' '}
            for buy &amp; hold. Steepness is return and depth is drawdown — the long
            flat-to-falling stretches are what holding this system through a bad year actually
            feels like.
          </>
        )}
      </p>
    </div>
  )
}

export function BacktestPanel({
  backtest,
  loading,
}: {
  backtest: Backtest | null
  loading: boolean
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

  // Declared as a list so a third rule slots in here rather than in the
  // table. `point_in_time` is deliberately NOT one of these: it differs in
  // universe, not in rules, and is drawn as an overlay on the chart above.
  const variants: Variant[] = [
    { label: 'As built (no stop)', run: backtest?.faithful },
    { label: 'With 2×ATR stop', run: backtest?.atr_stop },
  ].filter((v): v is Variant => v.run != null)

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
                python3 -m app.backtest --universe
                constituents:data/universes/sp500_constituents.csv,etfs --both-variants --save
              </code>{' '}
              from the backend folder to simulate this strategy over historical prices and populate
              this panel.
            </>
          }
        />
      </Card>
    )
  }

  // The other-universe curves are only worth drawing beside a run they
  // differ from in exactly one way — the universe. Against the ATR-stop
  // variant, or against a commodities run, two things would have changed
  // at once and the gap between the lines would no longer mean survivorship
  // bias.
  const comparable = run === backtest?.faithful && run.universe.includes('sp500')
  const pointInTime = comparable ? backtest?.point_in_time ?? null : null
  // Present only when the headline IS the members-at-the-time run (the API
  // decides that). The two lists that could not actually have been traded
  // then sit behind it: dashed (today's) and dotted (the snapshot).
  const currentList = comparable ? backtest?.current_list ?? null : null

  const overlays: Overlay[] = [
    ...(currentList
      ? [{ label: "Today's list", points: currentList.equity, width: 1.5, opacity: 0.75, dash: '5 3' }]
      : []),
    ...(pointInTime
      ? [
          {
            label: `${snapshotYear(pointInTime)} list`,
            points: pointInTime.equity,
            ...(currentList ? { dash: '1.5 3', opacity: 0.75 } : {}),
          },
        ]
      : []),
  ]

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
          strategyLabel={currentList ? 'Members at the time' : pointInTime ? "Today's list" : 'Strategy'}
          strategyStyle={currentList ? { width: 2.5 } : {}}
          overlays={overlays}
        />
        <ChartNote run={run} pointInTime={pointInTime} currentList={currentList} />

        <div className="grid gap-6 lg:grid-cols-2 mt-6">
          <div>
            <h3 className="text-xs font-semibold mb-1">vs buy &amp; hold</h3>
            <ComparisonTable run={run} />
          </div>
          {variants.length >= 2 && (
            <div>
              <h3 className="text-xs font-semibold mb-1">What the missing stop-loss costs</h3>
              <VariantComparison variants={variants} />
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
            exit is the Donchian breakdown, so a gap
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
