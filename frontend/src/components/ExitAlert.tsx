import { Fragment } from 'react'
import { AlertTriangle } from 'lucide-react'
import type { Holding } from '../lib/api'
import { TickerLink } from './TickerLink'

/**
 * The one thing worth interrupting the layout for: a position you hold has
 * closed below its exit level. Rendered above the grid so it's visible
 * without scrolling, and omitted entirely when there's nothing to say —
 * a permanent "0 exit signals" banner would train you to ignore it.
 */
export function ExitAlert({
  holdings,
  onSelectTicker,
}: {
  holdings: Holding[]
  onSelectTicker?: (ticker: string) => void
}) {
  const flagged = holdings.filter((h) => h.signal?.exit_signal).map((h) => h.ticker)
  if (flagged.length === 0) return null

  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/[0.08] px-4 py-3 mb-6"
    >
      <AlertTriangle size={16} className="text-destructive mt-0.5 shrink-0" />
      <div className="text-sm">
        <p className="font-medium">
          {flagged.length} exit {flagged.length === 1 ? 'signal' : 'signals'} on your holdings
        </p>
        <p className="text-muted-foreground text-xs mt-0.5">
          {/* Mapped rather than joined, so each symbol is its own link to
              its chart. The separators stay plain text between them. */}
          <span className="font-mono">
            {flagged.map((ticker, i) => (
              <Fragment key={ticker}>
                {i > 0 && ', '}
                <TickerLink ticker={ticker} onSelect={onSelectTicker} />
              </Fragment>
            ))}
          </span>{' '}
          closed below the 20-day Donchian low as of the last pipeline run.
        </p>
      </div>
    </div>
  )
}
