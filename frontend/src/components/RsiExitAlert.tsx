import { AlertTriangle } from 'lucide-react'
import type { RsiHolding } from '../lib/api'

/**
 * The RSI page's version of the exit banner.
 *
 * Worth its own wording rather than reusing the Donchian one: an exit here
 * is good news. The strategy bought a dip and the bounce has arrived, so
 * this is "take the profit", not "cut the loss" — and a banner that reads
 * like an emergency when it isn't one trains you to ignore the real ones.
 */
export function RsiExitAlert({ holdings }: { holdings: RsiHolding[] }) {
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
          {flagged.length} RSI exit {flagged.length === 1 ? 'signal' : 'signals'} on your holdings
        </p>
        <p className="text-muted-foreground text-xs mt-0.5">
          <span className="font-mono">{flagged.join(', ')}</span>{' '}
          {flagged.length === 1 ? 'has' : 'have'} either closed back above the 5-day average or
          pushed RSI(2) past 70 — where this strategy sells into the bounce. This says nothing about
          the Donchian page, which exits on a different rule entirely.
        </p>
      </div>
    </div>
  )
}
