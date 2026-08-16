import { ChevronLeft, ChevronRight } from 'lucide-react'
import { ROUTE_LABELS, type Route } from '../hooks/useHashRoute'
import { Button, cn } from './ui'

/**
 * Moves between the strategy pages.
 *
 * Two strategies, so this is a pair of arrows rather than tabs or a
 * dropdown: with one destination in each direction, an arrow says
 * everything a menu would and costs no vertical space. The destination is
 * named on the button rather than left to the icon — an unlabelled arrow
 * on a dashboard reads as "next page of results", which is the wrong idea
 * entirely.
 *
 * If a third strategy ever lands, this is the component that should become
 * a tab bar. The arrows stop scaling the moment "right" is ambiguous.
 */

const ORDER: Route[] = ['donchian', 'rsi']

export function StrategyNav({
  route,
  onNavigate,
}: {
  route: Route
  onNavigate: (route: Route) => void
}) {
  const index = ORDER.indexOf(route)
  const previous = index > 0 ? ORDER[index - 1] : null
  const next = index < ORDER.length - 1 ? ORDER[index + 1] : null

  return (
    <nav
      aria-label="Strategy"
      className="flex items-center justify-between gap-3 mb-6 rounded-lg border border-border bg-muted/40 px-3 py-2"
    >
      <div className="min-w-0">
        {previous ? (
          <Button
            variant="ghost"
            onClick={() => onNavigate(previous)}
            className="px-2"
            aria-label={`Back to ${ROUTE_LABELS[previous]}`}
          >
            <ChevronLeft size={14} />
            <span className="truncate">{ROUTE_LABELS[previous]}</span>
          </Button>
        ) : (
          // Placeholder keeps the current strategy's name centred whether
          // or not there's a page to the left of it.
          <span className="inline-block w-2" />
        )}
      </div>

      <p className="text-xs font-medium tracking-tight text-center truncate">{ROUTE_LABELS[route]}</p>

      <div className={cn('min-w-0 text-right')}>
        {next ? (
          <Button
            variant="ghost"
            onClick={() => onNavigate(next)}
            className="px-2"
            aria-label={`Go to ${ROUTE_LABELS[next]}`}
          >
            <span className="truncate">{ROUTE_LABELS[next]}</span>
            <ChevronRight size={14} />
          </Button>
        ) : (
          <span className="inline-block w-2" />
        )}
      </div>
    </nav>
  )
}
