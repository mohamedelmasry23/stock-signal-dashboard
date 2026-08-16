import { Plus, TrendingDown } from 'lucide-react'
import type { RsiEntryCandidate } from '../lib/api'
import { formatPrice, formatShares } from '../lib/format'
import { Button, Card, CardHeader, EmptyState, Skeleton } from './ui'

/**
 * The RSI(2) pullback's buy list.
 *
 * The Donchian panel's mirror image, and the icon is inverted on purpose:
 * that page buys things going up, this one buys things that just fell. A
 * candidate here is an index or sector fund in a confirmed uptrend (above
 * its 200-day average) that has just had a sharp two-day drop.
 *
 * Baskets only, never single companies — measured, not assumed: over
 * 2016-2026 this strategy returned 8% on S&P 500 constituents against
 * SPY's 353%. A basket's sharp drop is usually flow that partly reverses;
 * one company's may be news that doesn't.
 */

function DepthBar({ depth, max }: { depth: number; max: number }) {
  // Scaled against the deepest candidate on screen rather than the 0-5
  // range the trigger allows: on a quiet day every candidate sits just
  // under 5 and a fixed scale would render five identical stubs.
  const pct = max > 0 ? Math.max(4, (depth / max) * 100) : 4
  return (
    <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden" aria-hidden="true">
      <div
        className="h-full rounded-full bg-[var(--chart-1)] transition-[width] duration-500"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

function RsiCandidateRow({
  candidate,
  max,
  onAdd,
  busy,
}: {
  candidate: RsiEntryCandidate
  max: number
  onAdd: (ticker: string) => void
  busy: boolean
}) {
  const depth = candidate.oversold_depth ?? 0

  return (
    <div className="px-5 py-3 border-b border-border last:border-b-0">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-baseline gap-2.5 min-w-0">
          <span className="font-mono text-sm font-semibold tracking-tight">{candidate.ticker}</span>
          <span className="text-xs text-muted-foreground tnum">{formatPrice(candidate.close)}</span>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <span
            className="text-xs text-muted-foreground tnum"
            title="RSI(2) today. Below 5 is the entry trigger; 0 is as oversold as the indicator goes."
          >
            RSI {candidate.rsi?.toFixed(1) ?? '—'}
          </span>
          <Button
            variant="secondary"
            onClick={() => onAdd(candidate.ticker)}
            disabled={busy}
            aria-label={`Add ${candidate.ticker} to holdings`}
            className="px-2 py-1"
          >
            <Plus size={12} />
          </Button>
        </div>
      </div>

      <DepthBar depth={depth} max={max} />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-[11px] text-muted-foreground tnum">
        <span title="The 5-day average. This strategy exits when the close gets back above it, so it doubles as the target.">
          Exit above <span className="text-foreground">{formatPrice(candidate.sma_fast)}</span>
        </span>
        <span title="The 200-day average. Price has to be above this for an entry to fire at all — it's what separates a dip from a collapse.">
          Trend <span className="text-foreground">{formatPrice(candidate.sma_trend)}</span>
        </span>
        <span title="Shares to buy so that a stop-out costs your configured risk budget, based on the account equity shown above">
          {/* No unit suffix — for GC=F this figure is troy ounces, not shares. */}
          Suggested <span className="text-foreground">{formatShares(candidate.suggested_shares)}</span>
        </span>
      </div>
    </div>
  )
}

export function RsiEntryCandidates({
  candidates,
  loading,
  onAdd,
  busyTicker,
}: {
  candidates: RsiEntryCandidate[]
  loading: boolean
  onAdd: (ticker: string) => void
  busyTicker: string | null
}) {
  const max = candidates.reduce((m, c) => Math.max(m, c.oversold_depth ?? 0), 0)

  return (
    <Card>
      <CardHeader
        title="Index &amp; ETF Pullbacks"
        subtitle={
          loading
            ? undefined
            : candidates.length === 0
              ? 'No oversold baskets'
              : `Top ${candidates.length}, most oversold first`
        }
      />

      {loading ? (
        <div className="px-5 py-4 space-y-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-1.5 w-full" />
            </div>
          ))}
        </div>
      ) : candidates.length === 0 ? (
        <EmptyState
          icon={<TrendingDown size={28} strokeWidth={1.5} />}
          title="No pullback signals today"
          hint="No index, sector or bond ETF is both above its 200-day average and oversold on RSI(2). This is the normal state, not a fault: across the backtest the strategy averaged under a fifth of the account deployed."
        />
      ) : (
        <div>
          {candidates.map((c) => (
            <RsiCandidateRow
              key={c.ticker}
              candidate={c}
              max={max}
              onAdd={onAdd}
              busy={busyTicker === c.ticker}
            />
          ))}
        </div>
      )}
    </Card>
  )
}
