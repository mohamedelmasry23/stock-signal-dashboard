import { Activity, Radio } from 'lucide-react'
import type { Watch, WatchItem } from '../lib/api'
import { absoluteTime, formatPrice, relativeTime } from '../lib/format'
import { TickerLink } from './TickerLink'
import { Badge, Card, CardHeader, EmptyState, Skeleton, cn } from './ui'

/**
 * What is trading above its 55-day high right now — and nothing more.
 *
 * This panel is deliberately harder to act on than the one above it. It has
 * no Add button and no suggested position size, so it is structurally
 * incapable of starting a trade: the only affordance is a ticker that opens
 * a chart. That matters more than the prose, because it works on a reader
 * who never reads the prose.
 *
 * The reason is measured, not stylistic. Across 52,087 breakouts, 77.3%
 * crossed their level during the session rather than gapping through it,
 * and for those, entering at that day's close instead of the next open was
 * worth +0.04% — less than one side of costs. There is no measured edge in
 * acting early, so this reports and the pipeline decides.
 */

function StrengthBar({ atrs, max }: { atrs: number; max: number }) {
  // Deliberately not --chart-1, which EntryCandidates uses. The eye should
  // read this panel as secondary to the measured one.
  const pct = max > 0 ? Math.max(4, (atrs / max) * 100) : 4
  return (
    <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden" aria-hidden="true">
      <div
        className="h-full rounded-full bg-[var(--muted-foreground)] opacity-50 transition-[width] duration-500"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

function WatchRow({
  item,
  max,
  muted,
  onSelectTicker,
}: {
  item: WatchItem
  max: number
  muted: boolean
  onSelectTicker?: (ticker: string) => void
}) {
  const above = item.atrs_above
  return (
    <div className="px-5 py-3 border-b border-border last:border-b-0">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-baseline gap-2.5 min-w-0 flex-wrap">
          <TickerLink
            ticker={item.ticker}
            onSelect={onSelectTicker}
            className="font-mono text-sm font-semibold tracking-tight"
          />
          <span className="text-xs text-muted-foreground tnum">{formatPrice(item.price)}</span>
          {item.change_percent != null && (
            <span
              className={cn(
                'text-[11px] tnum',
                item.change_percent >= 0 ? 'text-positive' : 'text-destructive',
              )}
            >
              {item.change_percent >= 0 ? '↑' : '↓'} {Math.abs(item.change_percent).toFixed(2)}%
            </span>
          )}
          {/* Outline, never tone="entry" — that blue means a measured signal. */}
          {item.gap_open && (
            <Badge tone="outline" title="Opened already above the level rather than crossing during the session">
              gapped
            </Badge>
          )}
          {item.held && <Badge tone="muted">held</Badge>}
          {item.already_flagged && (
            <Badge tone="muted" title="The last pipeline run already flagged this as an entry signal">
              already flagged
            </Badge>
          )}
        </div>
        <span className="text-xs text-muted-foreground tnum shrink-0" title="ATRs above the 55-day high">
          {above == null ? '—' : `${above.toFixed(2)} ATR`}
        </span>
      </div>

      {!muted && above != null && <StrengthBar atrs={above} max={max} />}

      <p className="text-[11px] text-muted-foreground mt-2 tnum">
        55-day high {formatPrice(item.level)} · needs to close above it to be flagged
      </p>
    </div>
  )
}

function WatchNote({ aboveCount }: { aboveCount: number }) {
  const broad = aboveCount > 100
  return (
    <div className="mx-5 mb-4 rounded-lg border border-border bg-muted/40 px-4 py-3">
      <p className="flex items-center gap-2 text-xs font-medium">
        <Activity size={13} />
        Live prices, not signals
      </p>
      <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
        These names are trading above the level a close would have to beat. That is all it means.
        A name can appear at 11:00, fade, close below and never be flagged — that is correct, not a
        miss.{' '}
        <span className="text-foreground">
          Nothing here is backtested. Across 52,087 breakouts, 77% crossed their level during the
          session rather than gapping through, and for those, entering at that day's close instead
          of the next open was worth +0.04% — less than one side of costs.
        </span>{' '}
        So there is no measured edge in acting early. The measured system is Entry Candidates above,
        computed from completed closes; the pipeline decides after the bell.
        {broad && (
          <>
            {' '}
            {aboveCount} names above their 55-day high is a reading of the tape, not {aboveCount}{' '}
            opportunities.
          </>
        )}
      </p>
    </div>
  )
}

export function IntradayWatch({
  watch,
  loading,
  onSelectTicker,
}: {
  watch: Watch | null
  loading: boolean
  onSelectTicker?: (ticker: string) => void
}) {
  if (loading) {
    return (
      <Card className="mt-6">
        <CardHeader title="Above the 55-day high right now" />
        <div className="px-5 py-4 space-y-4">
          {[0, 1, 2].map((i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-1.5 w-full" />
            </div>
          ))}
        </div>
      </Card>
    )
  }

  if (!watch) return null

  // Never present a scan from a previous session as if it were today's —
  // not even dimmed. This used to also require an empty list, so a stored
  // list from any earlier day stayed on screen: when the schedule silently
  // stopped scanning, the panel showed a 17-day-old list as "Stale".
  const neverScanned = watch.scanned_at == null
  const wrongSession = !watch.current_session
  const noScanToday = neverScanned || wrongSession

  // Outside the session the last scan stays visible — it is the closest
  // live read on the day the pipeline is about to judge — but rendered
  // inert so it can't be mistaken for something still moving.
  const muted = !watch.market_open || watch.stale

  const max = watch.items.reduce((m, i) => Math.max(m, i.atrs_above ?? 0), 0)

  const badge = noScanToday ? null : !watch.market_open ? (
    <Badge tone="muted">Market closed</Badge>
  ) : watch.stale ? (
    <Badge tone="muted" title="The last scan is more than 45 minutes old">
      Stale
    </Badge>
  ) : (
    <Badge tone="outline" title="Live prices from the last intraday scan — not a signal">
      <Radio size={10} />
      Live · not a signal
    </Badge>
  )

  const subtitle = noScanToday
    ? undefined
    : [
        `As of ${absoluteTime(watch.scanned_at)} · ${relativeTime(watch.scanned_at)}`,
        watch.truncated ? `showing ${watch.items.length} of ${watch.above_count}` : null,
        watch.fetched < watch.universe_size * 0.9 && watch.universe_size > 0
          ? `only ${watch.fetched} of ${watch.universe_size} tickers fetched`
          : null,
      ]
        .filter(Boolean)
        .join(' · ')

  return (
    <Card className={cn('mt-6', muted && !noScanToday && 'opacity-70')}>
      <CardHeader title="Above the 55-day high right now" subtitle={subtitle} action={badge} />

      {noScanToday ? (
        <EmptyState
          icon={<Activity size={28} strokeWidth={1.5} />}
          title="No scan yet today"
          hint="The intraday scan runs four times during the US session, from about 9:45 to 15:45 ET. Until today's first scan this panel stays empty rather than showing an earlier day's list."
        />
      ) : watch.items.length === 0 ? (
        <EmptyState
          icon={<Activity size={28} strokeWidth={1.5} />}
          title="Nothing above its 55-day high"
          hint="The common case — a median day has around 24 across the whole universe, and plenty have none at all."
        />
      ) : (
        <>
          <div>
            {watch.items.map((item) => (
              <WatchRow
                key={item.ticker}
                item={item}
                max={max}
                muted={muted}
                onSelectTicker={onSelectTicker}
              />
            ))}
          </div>
          <div className="pt-4">
            <WatchNote aboveCount={watch.above_count} />
          </div>
        </>
      )}

      {!watch.market_open && !noScanToday && watch.items.length > 0 && (
        <p className="px-5 pb-4 text-[11px] text-muted-foreground">
          Market closed — this is the last look at today's session. The pipeline decides tonight.
        </p>
      )}
    </Card>
  )
}
