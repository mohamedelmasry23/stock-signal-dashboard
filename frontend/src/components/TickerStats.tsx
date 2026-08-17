import type { TickerDetail } from '../lib/api'
import { formatMoney, formatPrice, formatShares } from '../lib/format'
import { Card, StatTile } from './ui'

/**
 * The ticker's numbers in the units the strategy actually uses.
 *
 * Distance to exit is stated in ATRs rather than as a percentage, matching
 * how the stop is sized: 2 ATR of headroom means the same amount of risk
 * whether the ticker moves 0.5% or 5% on a normal day, which a percentage
 * doesn't. The holdings panel says the same thing as a percentage because
 * it's scanning many tickers at a glance; here there's room to be exact.
 */
export function TickerStats({ detail }: { detail: TickerDetail }) {
  const s = detail.stats
  const flagged = s.exit_signal

  const headroom =
    s.atrs_to_exit == null
      ? '—'
      : s.atrs_to_exit < 0
        ? 'below'
        : `${s.atrs_to_exit.toFixed(1)} ATR`

  return (
    <Card className="mt-6">
      <div className="px-5 py-4 grid grid-cols-2 sm:grid-cols-4 gap-4">
        <StatTile
          label="Last close"
          value={formatPrice(s.close)}
          sub={s.as_of_date ?? undefined}
          title="The last completed daily close — what the signal is computed from"
        />
        <StatTile
          label="55-day breakout level"
          value={formatPrice(s.donchian_high)}
          sub={
            s.breakout_strength != null
              ? `${s.breakout_strength >= 0 ? '+' : ''}${s.breakout_strength.toFixed(2)} ATR`
              : undefined
          }
          title="Close above this level is an entry signal"
        />
        <StatTile
          label="20-day exit level"
          value={formatPrice(s.donchian_low)}
          tone={flagged ? 'text-destructive' : undefined}
          sub={flagged ? 'close is below it' : undefined}
          title="Close below this level is an exit signal"
        />
        <StatTile
          label="Distance to exit"
          value={headroom}
          tone={flagged ? 'text-destructive' : undefined}
          title="How far the close sits above the 20-day low, in ATRs — the same unit the stop is sized in"
        />
        <StatTile
          label="ATR(20)"
          value={formatPrice(s.atr)}
          title="Average true range over 20 days — one day's typical move"
        />
        <StatTile
          label="Stop distance"
          value={formatPrice(s.stop_distance)}
          sub="2 x ATR"
          title="The distance a position is sized against, not an order that exists"
        />
        <StatTile
          label="Suggested size"
          value={formatShares(s.suggested_shares)}
          sub="at your current equity"
          title="Sized so a move of one stop distance costs the configured fraction of equity. Arithmetic, not a recommendation."
        />
        <StatTile
          label="Signals in view"
          value={`${detail.bars.entry_index.length} / ${detail.bars.exit_index.length}`}
          sub="entry / exit"
          title="How many times each signal fired over the range on screen"
        />
      </div>

      {detail.held && (
        <div className="px-5 pb-4">
          <div className="rounded-lg border border-border bg-muted/40 px-4 py-3 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 text-xs">
            <span className="text-muted-foreground">
              You hold{' '}
              {/* No unit suffix: the amount is in whatever the ticker is
                  quoted in — shares for an ETF, troy ounces for GC=F. */}
              <span className="text-foreground tnum font-medium">{formatShares(detail.shares)}</span>
            </span>
            <span className="text-muted-foreground">
              Worth{' '}
              <span className="text-foreground tnum font-medium">
                {formatMoney(detail.market_value)}
              </span>
            </span>
          </div>
        </div>
      )}

      {s.signal_source === 'computed' && (
        <div className="px-5 pb-4">
          <p className="text-[11px] text-muted-foreground leading-relaxed">
            These levels were computed here from Yahoo's history rather than read from a daily
            pipeline run — this ticker isn't in the scan universe, so nothing about it is stored.
          </p>
        </div>
      )}
    </Card>
  )
}
