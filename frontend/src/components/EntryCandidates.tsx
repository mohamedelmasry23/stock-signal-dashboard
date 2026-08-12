import { Plus, TrendingUp } from 'lucide-react'
import type { EntryCandidate } from '../lib/api'
import { formatPrice, formatShares } from '../lib/format'
import { Button, Card, CardHeader, EmptyState, Skeleton } from './ui'

function StrengthBar({ strength, max }: { strength: number; max: number }) {
  // Scaled against the strongest candidate on screen rather than an absolute
  // ceiling: breakout strength has no natural upper bound, so a fixed scale
  // would leave every bar stubby on a quiet day.
  const pct = max > 0 ? Math.max(4, (strength / max) * 100) : 4
  return (
    <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden" aria-hidden="true">
      <div
        className="h-full rounded-full bg-[var(--chart-1)] transition-[width] duration-500"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

function CandidateRow({
  candidate,
  max,
  onAdd,
  busy,
}: {
  candidate: EntryCandidate
  max: number
  onAdd: (ticker: string) => void
  busy: boolean
}) {
  const strength = candidate.breakout_strength ?? 0

  return (
    <div className="px-5 py-3 border-b border-border last:border-b-0">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-baseline gap-2.5 min-w-0">
          <span className="font-mono text-sm font-semibold tracking-tight">{candidate.ticker}</span>
          <span className="text-xs text-muted-foreground tnum">
            {formatPrice(candidate.close)}
          </span>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <span
            className="text-xs text-muted-foreground tnum"
            title="How many ATRs above the 55-day breakout level today's close printed"
          >
            {strength.toFixed(2)} ATR
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

      <StrengthBar strength={strength} max={max} />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-[11px] text-muted-foreground tnum">
        <span>
          Breakout level <span className="text-foreground">{formatPrice(candidate.donchian_high)}</span>
        </span>
        <span>
          ATR <span className="text-foreground">{formatPrice(candidate.atr)}</span>
        </span>
        <span title="Shares to buy so that a stop-out costs your configured risk budget, based on the account equity shown above">
          {/* No unit suffix — see the note in HoldingsPanel: for GC=F this
              figure is troy ounces, not shares. */}
          Suggested <span className="text-foreground">{formatShares(candidate.suggested_shares)}</span>
        </span>
      </div>
    </div>
  )
}

export function EntryCandidates({
  candidates,
  loading,
  onAdd,
  busyTicker,
}: {
  candidates: EntryCandidate[]
  loading: boolean
  onAdd: (ticker: string) => void
  busyTicker: string | null
}) {
  const max = candidates.reduce((m, c) => Math.max(m, c.breakout_strength ?? 0), 0)

  return (
    <Card>
      <CardHeader
        title="Entry Candidates"
        subtitle={
          loading
            ? undefined
            : candidates.length === 0
              ? 'No fresh breakouts'
              : `Top ${candidates.length}, ranked by breakout strength`
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
          icon={<TrendingUp size={28} strokeWidth={1.5} />}
          title="No entry signals today"
          hint="Nothing in the scan universe closed above its 55-day high. This is normal — breakouts cluster, and quiet days are the majority."
        />
      ) : (
        <div>
          {candidates.map((c) => (
            <CandidateRow
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
