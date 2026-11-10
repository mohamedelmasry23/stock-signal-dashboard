import { cn } from './ui'

/**
 * A ticker symbol that opens its detail chart.
 *
 * Renders a plain span when no handler is given, so a caller can't
 * accidentally ship a button that does nothing.
 *
 * Typography is passed in rather than owned here: the panels render tickers
 * at `text-sm`, the exit banner at the surrounding prose size. This
 * component owns only the interaction and the focus treatment, which is
 * the part that should be identical everywhere.
 *
 * Note what is deliberately *not* clickable: the row. Holdings rows already
 * contain an editable shares input and a delete button, so a clickable row
 * would nest interactive controls and turn "click into the field to correct
 * a share count" into a navigation.
 */
export function TickerLink({
  ticker,
  onSelect,
  className,
}: {
  ticker: string
  onSelect?: (ticker: string) => void
  className?: string
}) {
  if (!onSelect) return <span className={className}>{ticker}</span>

  return (
    <button
      type="button"
      onClick={() => onSelect(ticker)}
      aria-label={`Open ${ticker} detail chart`}
      className={cn(
        'rounded-sm cursor-pointer hover:underline underline-offset-2 decoration-muted-foreground',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        'focus-visible:ring-offset-1 focus-visible:ring-offset-background',
        className,
      )}
    >
      {ticker}
    </button>
  )
}
