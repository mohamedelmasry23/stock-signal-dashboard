import { AlertTriangle, ArrowDown, ArrowUp, Briefcase, Trash2 } from 'lucide-react'
import { useState } from 'react'
import type { Holding } from '../lib/api'
import { formatMoney } from '../lib/format'
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

/**
 * The session's move, from the previous close to the latest one. Null
 * rather than 0 when `prev_close` is missing — a ticker with a single bar
 * of history has an *unknown* move, and rendering that as a confident
 * "0.00%" would be a claim the data doesn't support.
 */
function dayChangePct(h: Holding): number | null {
  const s = h.signal
  if (s?.close == null || s.prev_close == null || s.prev_close <= 0) return null
  return ((s.close - s.prev_close) / s.prev_close) * 100
}

/**
 * What the position is worth. `shares` is in the ticker's own quote unit,
 * so this is the same arithmetic for shares of an ETF and troy ounces of
 * gold — which is exactly why per-ounce holdings need no special case.
 */
function marketValue(h: Holding): number | null {
  const s = h.signal
  if (h.shares == null || s?.close == null) return null
  return h.shares * s.close
}

function DayChange({ pct }: { pct: number | null }) {
  if (pct === null) {
    return (
      <span className="text-muted-foreground" title="No previous close available for this ticker">
        —
      </span>
    )
  }

  // Exactly flat gets neither colour nor arrow: it's neither a gain nor a
  // loss, and painting it green would overstate the move.
  const direction = pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat'

  return (
    <span
      className={cn(
        'inline-flex items-center gap-0.5 font-medium',
        direction === 'up' && 'text-positive',
        direction === 'down' && 'text-destructive',
        direction === 'flat' && 'text-muted-foreground',
      )}
      title="Move since the previous close, as of the last pipeline run"
    >
      {direction === 'up' && <ArrowUp size={11} strokeWidth={2.5} aria-hidden="true" />}
      {direction === 'down' && <ArrowDown size={11} strokeWidth={2.5} aria-hidden="true" />}
      {/* The arrow is decorative; the sign is spelled out for screen
          readers so the direction isn't carried by colour alone. */}
      <span className="sr-only">{direction === 'down' ? 'down' : direction === 'up' ? 'up' : ''}</span>
      {Math.abs(pct).toFixed(2)}%
    </span>
  )
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
  // The box shows what the position is worth, but the amount held is still
  // the only editable number — so it swaps to the raw amount while focused
  // and back to the dollar value on blur. Without this, showing the value
  // would have silently removed the only way to change a holding.
  const [editing, setEditing] = useState(false)
  const sig = holding.signal
  const isExit = sig?.exit_signal ?? false
  const headroom = exitHeadroom(holding)
  const value = marketValue(holding)

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
            value={editing ? sharesDraft : value === null ? '—' : formatMoney(value, 2)}
            onChange={(e) => setSharesDraft(e.target.value)}
            onFocus={() => {
              setSharesDraft(holding.shares?.toString() ?? '')
              setEditing(true)
            }}
            onBlur={() => {
              commitShares()
              setEditing(false)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') e.currentTarget.blur()
              if (e.key === 'Escape') {
                setSharesDraft(holding.shares?.toString() ?? '')
                e.currentTarget.blur()
              }
            }}
            disabled={busy}
            inputMode="decimal"
            aria-label={`Value of ${holding.ticker} — focus to edit the amount held`}
            title={
              value === null
                ? 'No value yet — click to enter the amount you hold'
                : `${holding.shares} × ${sig?.close?.toFixed(2)} — click to edit the amount held`
            }
            className="w-28 h-7 tnum text-right"
          />
          {/* No unit suffix: the amount is in whatever unit the ticker is
              quoted in, and holdings legitimately mix them — shares for
              GLD, troy ounces for GC=F. */}
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
          <DayChange pct={dayChangePct(holding)} />
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
          hint="Add a ticker above to track its exit signal. Enter the amount you hold in whatever unit the ticker is quoted in — shares for a stock or ETF, troy ounces for GC=F."
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
