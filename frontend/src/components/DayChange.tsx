import { ArrowDown, ArrowUp } from 'lucide-react'
import { cn } from './ui'

/** The day's move as a coloured, arrowed percentage. */
export function DayChange({ pct, live = false }: { pct: number | null; live?: boolean }) {
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
      title={
        live
          ? "Today's move, from Yahoo's own quote as of the last refresh"
          : 'Move since the previous close, as of the last pipeline run'
      }
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
