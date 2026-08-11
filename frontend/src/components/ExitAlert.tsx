import { AlertTriangle } from 'lucide-react'
import type { Holding } from '../lib/api'

/**
 * The one thing worth interrupting the layout for: a position you hold has
 * closed below its exit level. Rendered above the grid so it's visible
 * without scrolling, and omitted entirely when there's nothing to say —
 * a permanent "0 exit signals" banner would train you to ignore it.
 */
export function ExitAlert({ holdings }: { holdings: Holding[] }) {
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
          <span className="font-mono">{flagged.join(', ')}</span>{' '}
          closed below the 20-day Donchian low as of the last pipeline run.
        </p>
      </div>
    </div>
  )
}
