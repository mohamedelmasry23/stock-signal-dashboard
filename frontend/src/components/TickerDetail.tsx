import { AlertCircle, AlertTriangle, ChevronLeft, Plus, Trash2, TrendingUp } from 'lucide-react'
import { CHART_RANGES, type ChartRange, type TickerDetail as TickerDetailData } from '../lib/api'
import { formatPrice } from '../lib/format'
import { dayChangePct, isLive } from '../lib/holdings'
import { DayChange } from './DayChange'
import { PriceChart } from './PriceChart'
import { TickerStats } from './TickerStats'
import { Badge, Button, Card, CardHeader, EmptyState, Skeleton } from './ui'

const RANGE_LABELS: Record<ChartRange, string> = {
  '6M': 'the last six months',
  '1Y': 'the last year',
  '2Y': 'the last two years',
  '5Y': 'the last five years',
  MAX: 'the full history',
}

function RangeButtons({
  range,
  onChange,
}: {
  range: ChartRange
  onChange: (range: ChartRange) => void
}) {
  // Above the chart rather than in the CardHeader's action slot: the header
  // lays title and action side by side, and five buttons squeeze the title
  // to one word per line on a phone. Here they have the full width to wrap
  // into.
  return (
    <div
      role="group"
      aria-label="Chart range"
      className="flex items-center justify-end flex-wrap gap-0.5 mb-2"
    >
      {CHART_RANGES.map((r) => (
        <Button
          key={r}
          variant={r === range ? 'secondary' : 'ghost'}
          onClick={() => onChange(r)}
          aria-pressed={r === range}
          className="px-2"
        >
          {r}
        </Button>
      ))}
    </div>
  )
}

/**
 * One ticker's price history with the Donchian levels over it.
 *
 * The dashboard states a verdict — flagged for exit, or a fresh breakout —
 * without showing the shape of the move behind it. This page is the
 * evidence for that verdict, which is why the chart is the largest thing on
 * it and the stats sit underneath rather than competing for the top.
 *
 * Donchian only. The bands drawn here are the 55/20-day levels, so this is
 * reachable from the Donchian page alone; an RSI detail view would need its
 * own indicators rather than this one under a different heading.
 */
export function TickerDetail({
  ticker,
  detail,
  loading,
  error,
  range,
  onRangeChange,
  onBack,
  onAdd,
  onRemove,
  busy,
}: {
  ticker: string
  detail: TickerDetailData | null
  loading: boolean
  error: string | null
  range: ChartRange
  onRangeChange: (range: ChartRange) => void
  onBack: () => void
  onAdd: (ticker: string) => void
  onRemove: (ticker: string) => void
  busy: boolean
}) {
  const back = (
    <nav className="mb-6 rounded-lg border border-border bg-muted/40 px-3 py-2">
      <Button variant="ghost" onClick={onBack} className="px-2" aria-label="Back to the dashboard">
        <ChevronLeft size={14} />
        <span className="truncate">Donchian Breakout</span>
      </Button>
    </nav>
  )

  if (error) {
    return (
      <>
        {back}
        <div
          role="alert"
          className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/[0.08] px-4 py-3"
        >
          <AlertCircle size={16} className="text-destructive mt-0.5 shrink-0" />
          <div className="text-sm">
            <p className="font-medium">Couldn't load {ticker}</p>
            <p className="text-muted-foreground text-xs mt-0.5">{error}</p>
          </div>
        </div>
      </>
    )
  }

  if (loading || !detail) {
    return (
      <>
        {back}
        <Skeleton className="h-8 w-40 mb-2" />
        <Skeleton className="h-6 w-28 mb-6" />
        <Card>
          <div className="px-5 py-4">
            <Skeleton className="h-[320px] w-full" />
          </div>
        </Card>
      </>
    )
  }

  const s = detail.stats
  // dayChangePct only reads close/prev_close off `signal`, so the stats
  // block satisfies its structural shape without a wrapper type. Same
  // helper the holdings rows use, so the number can't disagree between the
  // two views.
  const priced = { quote: detail.quote, signal: { close: s.close, prev_close: s.prev_close } }
  const price = detail.quote?.price ?? s.close
  const openNow = detail.quote?.market_state === 'REGULAR'

  return (
    <>
      {back}

      <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="font-mono text-2xl font-semibold tracking-tight">{detail.ticker}</h1>
            {s.asset_class && <Badge tone="outline">{s.asset_class}</Badge>}
            {s.exit_signal && (
              <Badge tone="destructive" title="Close is below the 20-day Donchian low">
                <AlertTriangle size={10} />
                Exit
              </Badge>
            )}
            {s.entry_signal && (
              <Badge tone="entry" title="Close is above the 55-day Donchian high">
                <TrendingUp size={10} />
                Breakout
              </Badge>
            )}
          </div>
          {s.long_name && <p className="text-xs text-muted-foreground mt-1">{s.long_name}</p>}
        </div>

        <div className="flex items-center gap-4 shrink-0">
          <div className="text-right">
            <p className="text-2xl font-semibold tracking-tight tnum">{formatPrice(price)}</p>
            <p className="text-xs mt-0.5">
              <DayChange pct={dayChangePct(priced)} live={isLive(priced)} />
            </p>
          </div>
          {detail.quote && (
            <Badge
              tone={openNow ? 'entry' : 'muted'}
              title={
                openNow
                  ? "Live from Yahoo as of the last refresh. Signals stay end-of-day regardless."
                  : "Market is closed — this is the last session's final figure"
              }
            >
              {openNow ? 'Live' : 'At last close'}
            </Badge>
          )}
          {detail.held ? (
            <Button
              variant="destructiveGhost"
              onClick={() => onRemove(detail.ticker)}
              disabled={busy}
              className="px-2"
              aria-label={`Remove ${detail.ticker} from holdings`}
            >
              <Trash2 size={14} />
            </Button>
          ) : (
            <Button
              variant="secondary"
              onClick={() => onAdd(detail.ticker)}
              disabled={busy}
              aria-label={`Add ${detail.ticker} to holdings`}
            >
              <Plus size={14} />
              Hold
            </Button>
          )}
        </div>
      </div>

      <Card>
        <CardHeader
          title="Price & Donchian levels"
          subtitle={
            detail.partial_last_bar
              ? "Today's bar is still trading — it's drawn dimmed and carries no signal marker"
              : 'Daily bars from Yahoo, with the levels the strategy compares each close against'
          }
        />
        <div className="px-5 py-4">
          <RangeButtons range={range} onChange={onRangeChange} />
          {detail.history_available ? (
            <PriceChart
              bars={detail.bars}
              partialLastBar={detail.partial_last_bar}
              ticker={detail.ticker}
              rangeLabel={RANGE_LABELS[range]}
            />
          ) : (
            <EmptyState
              icon={<AlertCircle size={28} strokeWidth={1.5} />}
              title="No price history available"
              hint="Yahoo returned nothing for this ticker just now. The levels below are the stored ones from the last pipeline run, so they're still good — it's the chart that's missing."
            />
          )}
        </div>
      </Card>

      <TickerStats detail={detail} />

      <p className="mt-6 text-[11px] text-muted-foreground leading-relaxed">
        Levels and signals come from completed daily closes, the same figures the dashboard shows.
        A live price changes what the position is worth, never when it exits — an exit that fired
        on an intraday dip which recovered by the close would be a different strategy from the one
        that was backtested.
      </p>
    </>
  )
}
