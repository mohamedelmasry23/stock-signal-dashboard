import { AlertTriangle, Briefcase, Trash2 } from 'lucide-react'
import { useState } from 'react'
import type { RsiHolding } from '../lib/api'
import { formatMoney, formatPrice } from '../lib/format'
import { dayChangePct, isLive } from '../lib/holdings'
import { AddHoldingForm } from './AddHoldingForm'
import { DayChange } from './DayChange'
import { Badge, Button, Card, CardHeader, EmptyState, Input, Skeleton, cn } from './ui'

/**
 * Your holdings as the RSI(2) strategy sees them.
 *
 * Structurally the Donchian panel, with one deliberate difference in
 * meaning: this strategy exits *upward*. Donchian holds until price breaks
 * down through the 20-day low, whereas this one sells into strength — as
 * soon as RSI(2) clears 70 or the close gets back above its 5-day average.
 * So "headroom" here is room left before the bounce completes, not before
 * a fall. The same position can legitimately be an exit on this page and a
 * hold on the other.
 */

/** Percent below the 5-day average — the room left before this position
 *  hits its exit. Negative would mean the close is already above it, which
 *  is an exit, so the caller only shows this when it isn't one. */
function bounceHeadroom(h: RsiHolding): number | null {
  const s = h.signal
  if (!s?.close || s.sma_fast == null || s.close <= 0) return null
  return ((s.sma_fast - s.close) / s.close) * 100
}

function RsiHoldingRow({
  holding,
  onRemove,
  onUpdateShares,
  busy,
}: {
  holding: RsiHolding
  onRemove: (ticker: string) => void
  onUpdateShares: (ticker: string, shares: number | null) => void
  busy: boolean
}) {
  const [sharesDraft, setSharesDraft] = useState(holding.shares?.toString() ?? '')
  const [editing, setEditing] = useState(false)
  const sig = holding.signal
  const isExit = sig?.exit_signal ?? false
  const headroom = bounceHeadroom(holding)
  const value = holding.market_value

  function commitShares() {
    const trimmed = sharesDraft.trim()
    const parsed = trimmed === '' ? null : Number(trimmed)
    if (parsed !== null && (Number.isNaN(parsed) || parsed < 0)) {
      setSharesDraft(holding.shares?.toString() ?? '') // reject junk, restore
      return
    }
    if (parsed !== holding.shares) onUpdateShares(holding.ticker, parsed)
  }

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
            <Badge
              tone="destructive"
              title="RSI(2) is above 70 or the close is back above its 5-day average — this strategy takes the bounce here"
            >
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
                : `${holding.shares} × ${(holding.quote?.price ?? sig?.close)?.toFixed(2)}` +
                  `${isLive(holding) ? ' (live)' : ' (last close)'} — click to edit the amount held`
            }
            className="w-28 h-7 tnum text-right"
          />
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
          <DayChange pct={dayChangePct(holding)} live={isLive(holding)} />
          {sig.rsi != null && (
            <span title="RSI(2), Wilder-smoothed. Above 70 is one of this strategy's two exits.">
              RSI <span className={cn('text-foreground', isExit && 'text-destructive font-medium')}>
                {sig.rsi.toFixed(1)}
              </span>
            </span>
          )}
          {isExit ? (
            <span className="text-destructive font-medium">bounce complete</span>
          ) : (
            headroom !== null && (
              <span title="How far the close sits below its 5-day average — the room left before this strategy exits">
                {headroom.toFixed(1)}% below {formatPrice(sig.sma_fast)} exit
              </span>
            )
          )}
        </div>
      ) : (
        <p className="mt-2 text-[11px] text-muted-foreground italic">
          No RSI signal yet — run the pipeline to compute one for this ticker.
        </p>
      )}
    </div>
  )
}

export function RsiHoldingsPanel({
  holdings,
  loading,
  busyTicker,
  onAdd,
  onRemove,
  onUpdateShares,
  addError,
}: {
  holdings: RsiHolding[]
  loading: boolean
  busyTicker: string | null
  onAdd: (ticker: string, shares: number | null) => Promise<void>
  onRemove: (ticker: string) => void
  onUpdateShares: (ticker: string, shares: number | null) => void
  addError: string | null
}) {
  // Exit-flagged first, then alphabetical — same ordering rule as the
  // Donchian panel, so moving between pages doesn't reshuffle the list for
  // reasons that have nothing to do with the strategies.
  const sorted = [...holdings].sort((a, b) => {
    const ax = a.signal?.exit_signal ? 0 : 1
    const bx = b.signal?.exit_signal ? 0 : 1
    return ax - bx || a.ticker.localeCompare(b.ticker)
  })

  const exitCount = holdings.filter((h) => h.signal?.exit_signal).length
  const openNow = holdings.some((h) => h.quote?.market_state === 'REGULAR')
  const quoted = holdings.some((h) => h.quote?.price != null)

  return (
    <Card>
      <CardHeader
        title="Your Holdings"
        action={
          quoted ? (
            <Badge
              tone={openNow ? 'entry' : 'muted'}
              title={
                openNow
                  ? 'Prices and day-change are live from Yahoo, refreshed when you hit Refresh'
                  : "Market is closed — these are the last session's final figures"
              }
            >
              {openNow ? 'Live' : 'At last close'}
            </Badge>
          ) : undefined
        }
        subtitle={
          loading
            ? undefined
            : holdings.length === 0
              ? 'Nothing tracked yet'
              : `${holdings.length} position${holdings.length === 1 ? '' : 's'}${
                  exitCount > 0 ? ` · ${exitCount} flagged by RSI` : ' · no RSI exit signals'
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
          hint="Add a ticker above to track it. Holdings are shared across both strategies — only the exit rules differ, so anything you own gets an RSI exit signal here even if it's a single stock this page would never suggest buying."
        />
      ) : (
        <div>
          {sorted.map((h) => (
            <RsiHoldingRow
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
