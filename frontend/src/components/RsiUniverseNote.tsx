import { Layers } from 'lucide-react'

/**
 * Why this page scans baskets and not companies.
 *
 * Worth a permanent fixture rather than a one-line footnote: the universe
 * is the single biggest design decision on this page, it was changed for a
 * measured reason, and without it stated the obvious question — "why can't
 * I see NVDA here?" — has no answer on screen.
 */
export function RsiUniverseNote() {
  return (
    <div className="mb-6 rounded-lg border border-border bg-muted/40 px-4 py-3">
      <p className="flex items-center gap-2 text-xs font-medium">
        <Layers size={13} />
        Indexes and ETFs only
      </p>
      <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
        This strategy scans liquid broad-index, sector, style and bond ETFs — never individual
        companies, and no longer the commodity funds. Both exclusions were measured, not assumed.{' '}
        <span className="text-foreground">
          Over S&amp;P 500 constituents these rules returned 8% against SPY's 353% with a 46%
          drawdown; keeping the thin commodity funds cost Sharpe 0.61 → 0.43.
        </span>{' '}
        Mean reversion bets that a sharp drop partly reverses, which holds up far better for a
        basket than for one company: an index falling 4% is usually flow, while one stock falling 4%
        may have a reason that never reverses. Read the backtest below as a diversifier rather than
        a total-return vehicle — it is invested about a fifth of the time, which the band under the
        chart shows directly. Holdings are shared with the Donchian page, so anything you own still
        appears here with its exit signal; it just can't be suggested as a new buy.
      </p>
    </div>
  )
}
