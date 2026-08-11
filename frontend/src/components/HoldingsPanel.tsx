import { AlertTriangle, Briefcase, Trash2 } from 'lucide-react'
import { useState } from 'react'
import type { Holding } from '../lib/api'
import { formatPrice } from '../lib/format'
import { AddHoldingForm } from './AddHoldingForm'
import { Badge, Button, Card, CardHeader, EmptyState, Input, Skeleton, cn } from './ui'

/**
 * How far today's close sits above the 20-day Donchian low, as a percentage.
 * The exit triggers when close drops below that level, so this is "headroom
 * before this position signals an exit" — the number you actually want when
 * scanning a portfolio, and one the API doesn't return directly.
 */
function exitHeadroom(h: Holding): number | null {
  const s = h.signal
  if (!s?.close || s.donchian_low == null || s.close <= 0) return null
  return ((s.close - s.donchian_low) / s.close) * 100
}

function HoldingRow({
  holding,
  onRemove,
  onUpdateShares,
  busy,
}: {
  holding: Holding
  onRemove: (ticker: string) => void
  onUpdateShares: (ticker: string, shares: number | null) => void
  busy: boolean
}) {
  const [sharesDraft, setSharesDraft] = useState(holding.shares?.toString() ?? '')
  const sig = holding.signal
  const isExit = sig?.exit_signal ?? false
  const headroom = exitHeadroom(holding)

  function commitShares() {
    const trimmed = sharesDraft.trim()
    const parsed = trimmed === '' ? null : Number(trimmed)
    if (parsed !== null && (Number.isNaN(parsed) || parsed < 0)) {
      setSharesDraft(holding.shares?.toString() ?? '') // reject junk, restore
      return
    }
    if (parsed !== holding.shares) onUpdateShares(holding.ticker, parsed)
  }

  // Two lines rather than one wide row: this panel sits in a ~550px column
  // on desktop, so a single-row layout with all the stats inline cramps as
  // soon as the grid goes 2-up. Viewport breakpoints can't see that — the
  // container is narrow while the viewport is wide.
  return (
    <div
      className={cn(
        'px-5 py-3 border-b border-border last:border-b-0 transition-colors',
        isExit && 'bg-destructive/[0.06]',
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-sm font-semibold tracking-tight">{holding.ticker}</span>
          {isExit && (
            <Badge tone="destructive" title="Close is below the 20-day Donchian low">
              <AlertTriangle size={10} />
              Exit
            </Badge>
          )}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <Input
            value={sharesDraft}
            onChange={(e) => setSharesDraft(e.target.value)}
            onBlur={commitShares}
            onKeyDown={(e) => {
              if (e.key === 'Enter') e.currentTarget.blur()
              if (e.key === 'Escape') {
                setSharesDraft(holding.shares?.toString() ?? '')
                e.currentTarget.blur()
              }
            }}
            disabled={busy}
            inputMode="decimal"
            placeholder="—"
            aria-label={`Shares held of ${holding.ticker}`}
            className="w-20 h-7 tnum text-right"
          />
          <span className="text-[11px] text-muted-foreground">sh</span>
          <Button
            variant="destructiveGhost"
            onClick={() => onRemove(holding.ticker)}
            disabled={busy}
            aria-label={`Remove ${holding.ticker} from holdings`}
            className="px-2"
          >
            <Trash2 size={13} />
          </Button>
        </div>
      </div>

      {sig ? (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-[11px] text-muted-foreground tnum">
          <span>
            Close <span className="text-foreground">{formatPrice(sig.close)}</span>
          </span>
          <span>
            Exit level <span className="text-foreground">{formatPrice(sig.donchian_low)}</span>
          </span>
          {headroom !== null && (
            <span
              className={cn(isExit && 'text-destructive font-medium')}
              title="Percentage the close sits above the exit trigger level"
            >
              {isExit ? 'below trigger' : `${headroom.toFixed(1)}% above exit`}
            </span>
          )}
        </div>
      ) : (
        <p className="mt-2 text-[11px] text-muted-foreground italic">
          No signal yet — run the pipeline to compute one for this ticker.
        </p>
      )}
    </div>
  )
}

export function HoldingsPanel({
  holdings,
  loading,
  busyTicker,
  onAdd,
  onRemove,
  onUpdateShares,
  addError,
}: {
  holdings: Holding[]
  loading: boolean
  busyTicker: string | null
  onAdd: (ticker: string, shares: number | null) => Promise<void>
  onRemove: (ticker: string) => void
  onUpdateShares: (ticker: string, shares: number | null) => void
  addError: string | null
}) {
  // Exit-flagged holdings first — that's the whole point of the dashboard.
  // Stable alphabetical within each group so rows don't shuffle on refresh.
  const sorted = [...holdings].sort((a, b) => {
    const ax = a.signal?.exit_signal ? 0 : 1
    const bx = b.signal?.exit_signal ? 0 : 1
    return ax - bx || a.ticker.localeCompare(b.ticker)
  })

  const exitCount = holdings.filter((h) => h.signal?.exit_signal).length

  return (
    <Card>
      <CardHeader
        title="Your Holdings"
        subtitle={
          loading
            ? undefined
            : holdings.length === 0
              ? 'Nothing tracked yet'
              : `${holdings.length} position${holdings.length === 1 ? '' : 's'}${
                  exitCount > 0 ? ` · ${exitCount} flagged for exit` : ' · no exit signals'
                }`
        }
      />

      <AddHoldingForm onAdd={onAdd} error={addError} />

      {loading ? (
        <div className="px-5 py-4 space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      ) : sorted.length === 0 ? (
        <EmptyState
          icon={<Briefcase size={28} strokeWidth={1.5} />}
          title="No holdings yet"
          hint="Add a ticker above, or drop a Wealthsimple activity CSV into backend/data/imports/ and run the pipeline to sync automatically."
        />
      ) : (
        <div>
          {sorted.map((h) => (
            <HoldingRow
              key={h.ticker}
              holding={h}
              onRemove={onRemove}
              onUpdateShares={onUpdateShares}
              busy={busyTicker === h.ticker}
            />
          ))}
        </div>
      )}
    </Card>
  )
}
