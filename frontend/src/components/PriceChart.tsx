import { useCallback, useMemo, useState } from 'react'
import type { TickerBars } from '../lib/api'
import { formatPrice } from '../lib/format'

/**
 * Daily price with the Donchian levels drawn over it, as inline SVG.
 *
 * Hand-rolled for the same reason EquityChart is: the project has
 * deliberately stayed dependency-light, and a charting library would be
 * ~100kB plus a theming layer to draw what amounts to some rects, two
 * polylines and a shaded band. The cost that *is* real here is the
 * crosshair — no hover machinery existed anywhere in this codebase before
 * this component, so it's built from pointer events and one absolutely
 * positioned div.
 *
 * Two deliberate departures from EquityChart, both of which look like
 * mistakes unless you know why:
 *
 * **The x axis is index-based, not time-based.** EquityChart argues for a
 * time axis and is right to: it plots two backtest runs that sampled
 * different dates, so array position would stretch one against the other.
 * Here there is a single evenly-sampled series, and candles need uniform
 * slot width — a time scale would open visible gaps across weekends and
 * holidays and make every candle a different width.
 *
 * **Price labels are on the right.** On a price chart the number you read
 * is the latest one, and it should sit next to the newest bar rather than
 * at the far end of the plot. It also gives the crosshair's price chip
 * somewhere useful to land.
 *
 * Colours are CSS variables throughout, never Tailwind colour classes, so
 * the whole thing theme-switches without a re-render.
 */

const WIDTH = 900
const PRICE_H = 300
const GAP = 6
const VOL_H = 48
const AXIS_H = 20
const PAD = { top: 10, right: 56, left: 8 }

const HEIGHT = PAD.top + PRICE_H + GAP + VOL_H + AXIS_H
const VOL_TOP = PAD.top + PRICE_H + GAP
const PLOT_W = WIDTH - PAD.left - PAD.right

// A candle body needs roughly 3px to read as a body rather than as a line,
// and the plot is ~836px wide, so about 278 bars is the ceiling. 260 is a
// round number just under it, and happens to be about a trading year: 6M
// and 1Y draw candles, 2Y and longer switch to a close line.
const CANDLE_MAX_BARS = 260

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/**
 * Month and year come from slicing the ISO string, never from `new Date()`.
 * `new Date("2026-08-17")` is parsed as UTC midnight, so `getMonth()` in any
 * negative-offset timezone returns the *previous* day's month — a real bug
 * for a US reader, and one that would only ever show up on bars that happen
 * to sit on a month boundary.
 */
function monthOf(iso: string): string {
  return MONTHS[Number(iso.slice(5, 7)) - 1] ?? ''
}

function humanDate(iso: string): string {
  return `${monthOf(iso)} ${Number(iso.slice(8, 10))}, ${iso.slice(0, 4)}`
}

/** Round price-axis values: 400, 420, 440 rather than 388.23, 396.71. */
function niceTicks(min: number, max: number, target = 5): number[] {
  if (!(max > min)) return [min]
  const raw = (max - min) / target
  const magnitude = Math.pow(10, Math.floor(Math.log10(raw)))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? magnitude * 10
  const out: number[] = []
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-6; v += step) {
    out.push(Number(v.toFixed(10)))
  }
  return out
}

/**
 * Tick positions on real calendar boundaries — month starts, falling back to
 * quarters and then years as the range widens. Evenly spaced positions would
 * put labels on arbitrary Tuesdays.
 */
function dateTicks(dates: string[]): number[] {
  const changes: number[] = []
  for (let i = 1; i < dates.length; i++) {
    if (dates[i].slice(0, 7) !== dates[i - 1].slice(0, 7)) changes.push(i)
  }

  let ticks = changes
  if (ticks.length > 8) {
    ticks = changes.filter((i) => ['01', '04', '07', '10'].includes(dates[i].slice(5, 7)))
  }
  if (ticks.length > 8) ticks = changes.filter((i) => dates[i].slice(5, 7) === '01')
  if (ticks.length > 8) {
    const stride = Math.ceil(ticks.length / 8)
    ticks = ticks.filter((_, k) => k % stride === 0)
  }
  return ticks
}

/**
 * Stretches of consecutive bars where both bands exist.
 *
 * The shaded channel has to be built run by run rather than as one polygon
 * over every index. The bands are null through the 55-bar warm-up, and a
 * single polygon spanning those nulls collapses to the origin and paints a
 * spike across the chart. This is the part of a shaded band that is most
 * often quietly wrong.
 */
function bandRuns(high: (number | null)[], low: (number | null)[]): [number, number][] {
  const runs: [number, number][] = []
  let start = -1
  for (let i = 0; i < high.length; i++) {
    const present = high[i] != null && low[i] != null
    if (present && start < 0) start = i
    if (!present && start >= 0) {
      if (i - start > 1) runs.push([start, i - 1])
      start = -1
    }
  }
  if (start >= 0 && high.length - start > 1) runs.push([start, high.length - 1])
  return runs
}

function compactVolume(v: number | null): string {
  if (v == null) return '—'
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`
  return String(Math.round(v))
}

export function PriceChart({
  bars,
  partialLastBar = false,
  ticker,
  rangeLabel,
}: {
  bars: TickerBars
  partialLastBar?: boolean
  ticker: string
  rangeLabel?: string
}) {
  const [hover, setHover] = useState<number | null>(null)

  const n = bars.dates.length

  const geometry = useMemo(() => {
    if (n === 0) return null

    const values: number[] = []
    for (let i = 0; i < n; i++) {
      const candidates = [bars.low[i], bars.high[i], bars.donchian_low[i], bars.donchian_high[i]]
      for (const v of candidates) if (v != null) values.push(v)
    }
    if (values.length === 0) return null

    // The bands have to be inside the domain or the shaded channel clips
    // against the frame rather than being visible as a channel.
    const lo = Math.min(...values)
    const hi = Math.max(...values)
    const pad = (hi - lo) * 0.02 || Math.abs(hi) * 0.02 || 1
    const min = lo - pad
    const max = hi + pad

    const volumes = bars.volume.filter((v): v is number => v != null)
    const volMax = volumes.length ? Math.max(...volumes) : 0

    const step = PLOT_W / n
    return {
      min,
      max,
      volMax,
      step,
      xFor: (i: number) => PAD.left + (i + 0.5) * step,
      priceY: (v: number) => PAD.top + PRICE_H * (1 - (v - min) / (max - min)),
      volY: (v: number) => VOL_TOP + VOL_H * (1 - (volMax > 0 ? v / volMax : 0)),
    }
  }, [bars, n])

  const pointerIndex = useCallback(
    (clientX: number, target: SVGSVGElement) => {
      if (!geometry) return null
      const rect = target.getBoundingClientRect()
      if (rect.width === 0) return null
      // The SVG has a viewBox and is rendered `w-full h-auto`, so client
      // pixels are not viewBox units. h-auto preserves the aspect ratio, so
      // a single scale factor is exact and preserveAspectRatio's
      // letterboxing never applies.
      const vx = (clientX - rect.left) * (WIDTH / rect.width)
      const i = Math.floor((vx - PAD.left) / geometry.step)
      return Math.min(n - 1, Math.max(0, i))
    },
    [geometry, n],
  )

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<SVGSVGElement>) => {
      const jump = e.shiftKey ? 10 : 1
      let next: number | null = null
      if (e.key === 'ArrowRight') next = Math.min(n - 1, (hover ?? n - 1) + jump)
      else if (e.key === 'ArrowLeft') next = Math.max(0, (hover ?? n - 1) - jump)
      else if (e.key === 'Home') next = 0
      else if (e.key === 'End') next = n - 1
      else if (e.key === 'Escape') {
        setHover(null)
        return
      } else return

      // Without this the page scrolls under the arrow keys while stepping.
      e.preventDefault()
      setHover(next)
    },
    [hover, n],
  )

  if (!geometry || n === 0) return null

  const { min, max, volMax, step, xFor, priceY, volY } = geometry
  const drawCandles = n <= CANDLE_MAX_BARS
  const bodyWidth = Math.max(1, step * 0.6)

  const priceTicks = niceTicks(min, max)
  const xTicks = dateTicks(bars.dates)

  // Breakouts cluster, so a five-year range can pile forty triangles into a
  // few pixels. One per pixel column is all that can be seen anyway.
  const thin = (indices: number[]) => {
    const seen = new Set<number>()
    return indices.filter((i) => {
      const px = Math.round(xFor(i))
      if (seen.has(px)) return false
      seen.add(px)
      return true
    })
  }

  const closePath = (() => {
    let path = ''
    let penDown = false
    for (let i = 0; i < n; i++) {
      const v = bars.close[i]
      if (v == null) {
        penDown = false // a gap breaks the line rather than drawing across it
        continue
      }
      path += `${penDown ? 'L' : 'M'}${xFor(i).toFixed(2)},${priceY(v).toFixed(2)} `
      penDown = true
    }
    return path.trim()
  })()

  const bandPath = (series: (number | null)[]) => {
    let path = ''
    let penDown = false
    for (let i = 0; i < n; i++) {
      const v = series[i]
      if (v == null) {
        penDown = false
        continue
      }
      path += `${penDown ? 'L' : 'M'}${xFor(i).toFixed(2)},${priceY(v).toFixed(2)} `
      penDown = true
    }
    return path.trim()
  }

  const channelPath = bandRuns(bars.donchian_high, bars.donchian_low)
    .map(([from, to]) => {
      let d = `M${xFor(from).toFixed(2)},${priceY(bars.donchian_high[from] as number).toFixed(2)}`
      for (let i = from + 1; i <= to; i++) {
        d += `L${xFor(i).toFixed(2)},${priceY(bars.donchian_high[i] as number).toFixed(2)}`
      }
      for (let i = to; i >= from; i--) {
        d += `L${xFor(i).toFixed(2)},${priceY(bars.donchian_low[i] as number).toFixed(2)}`
      }
      return `${d}Z`
    })
    .join(' ')

  const hovered =
    hover != null
      ? {
          date: bars.dates[hover],
          open: bars.open[hover],
          high: bars.high[hover],
          low: bars.low[hover],
          close: bars.close[hover],
          volume: bars.volume[hover],
          high55: bars.donchian_high[hover],
          low20: bars.donchian_low[hover],
          entry: bars.entry_index.includes(hover),
          exit: bars.exit_index.includes(hover),
          partial: partialLastBar && hover === n - 1,
        }
      : null

  const atrsToExit =
    hovered && hovered.close != null && hovered.low20 != null
      ? hovered.close - hovered.low20
      : null

  const lastClose = bars.close[n - 1]

  return (
    <figure className="m-0">
      <div className="relative">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="w-full h-auto touch-pan-y focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
          role="img"
          tabIndex={0}
          aria-label={
            `${ticker} daily prices${rangeLabel ? ` over ${rangeLabel}` : ''}, ` +
            `${bars.dates[0]} to ${bars.dates[n - 1]}, with the 55-day breakout level and ` +
            `20-day exit level. Last close ${formatPrice(lastClose)}. ` +
            `${bars.entry_index.length} entry and ${bars.exit_index.length} exit signals in view. ` +
            'Focus this chart and use the arrow keys to read individual bars.'
          }
          onPointerMove={(e) => setHover(pointerIndex(e.clientX, e.currentTarget))}
          onPointerDown={(e) => setHover(pointerIndex(e.clientX, e.currentTarget))}
          onPointerLeave={() => setHover(null)}
          onFocus={() => setHover((h) => h ?? n - 1)}
          onBlur={() => setHover(null)}
          onKeyDown={onKeyDown}
        >
          {/* One transparent hit target rather than a handler per bar: 260
              handlers would be wasteful and would leave dead gaps between
              candles. Note `transparent` receives pointer events where
              `none` would not. */}
          <rect
            x={PAD.left}
            y={PAD.top}
            width={PLOT_W}
            height={PRICE_H + GAP + VOL_H}
            fill="transparent"
          />

          {priceTicks.map((value) => (
            <g key={value}>
              <line
                x1={PAD.left}
                x2={WIDTH - PAD.right}
                y1={priceY(value)}
                y2={priceY(value)}
                stroke="var(--border)"
                strokeWidth={1}
              />
              <text
                x={WIDTH - PAD.right + 6}
                y={priceY(value)}
                dominantBaseline="middle"
                fontSize={10}
                fill="var(--muted-foreground)"
              >
                {formatPrice(value)}
              </text>
            </g>
          ))}

          {/* The channel, under everything: a 55-day high above and a
              20-day low below. Not a symmetric band — the two edges are
              different rules, which is why the low is dashed. */}
          {/* Kept very faint. The 55-day high and 20-day low are far apart
              by construction, so this band covers most of the plot — at any
              real opacity it stops being a backdrop and starts competing
              with the candles it exists to frame. */}
          {channelPath && (
            <path d={channelPath} fill="var(--chart-2)" opacity={0.07} stroke="none" />
          )}
          <path
            d={bandPath(bars.donchian_high)}
            fill="none"
            stroke="var(--chart-2)"
            strokeWidth={1}
            opacity={0.85}
          />
          <path
            d={bandPath(bars.donchian_low)}
            fill="none"
            stroke="var(--chart-2)"
            strokeWidth={1}
            strokeDasharray="4 3"
            opacity={0.85}
          />

          {drawCandles ? (
            <g>
              {bars.dates.map((date, i) => {
                const o = bars.open[i]
                const h = bars.high[i]
                const l = bars.low[i]
                const c = bars.close[i]
                if (o == null || h == null || l == null || c == null) return null
                const up = c >= o
                const colour = up ? 'var(--positive)' : 'var(--destructive)'
                const yO = priceY(o)
                const yC = priceY(c)
                return (
                  <g
                    key={date}
                    opacity={partialLastBar && i === n - 1 ? 0.55 : 1}
                    fill={colour}
                    stroke={colour}
                  >
                    <line
                      x1={xFor(i)}
                      x2={xFor(i)}
                      y1={priceY(h)}
                      y2={priceY(l)}
                      strokeWidth={1}
                    />
                    <rect
                      x={xFor(i) - bodyWidth / 2}
                      y={Math.min(yO, yC)}
                      width={bodyWidth}
                      // max(1) so a doji stays visible instead of vanishing
                      // into a zero-height rect.
                      height={Math.max(1, Math.abs(yO - yC))}
                      stroke="none"
                    />
                  </g>
                )
              })}
            </g>
          ) : (
            <path
              d={closePath}
              fill="none"
              stroke="var(--foreground)"
              strokeWidth={1.5}
              strokeLinejoin="round"
            />
          )}

          {/* Signal markers. Entry is the same blue as Badge tone="entry" —
              the palette has no green, and blue/red stays legible for
              red-green colour vision deficiency (see ui.tsx). */}
          {thin(bars.entry_index).map((i) => {
            const low = bars.low[i]
            if (low == null) return null
            const y = priceY(low) + 5
            return (
              <path
                key={`entry-${i}`}
                d={`M${xFor(i) - 4},${y + 6} L${xFor(i) + 4},${y + 6} L${xFor(i)},${y} Z`}
                fill="var(--chart-2)"
              />
            )
          })}
          {thin(bars.exit_index).map((i) => {
            const high = bars.high[i]
            if (high == null) return null
            const y = priceY(high) - 5
            return (
              <path
                key={`exit-${i}`}
                d={`M${xFor(i) - 4},${y - 6} L${xFor(i) + 4},${y - 6} L${xFor(i)},${y} Z`}
                fill="var(--destructive)"
              />
            )
          })}

          {/* Volume. Deliberately one flat colour rather than tinted by the
              day's direction: colouring a thousand bars by direction is
              noise, and it would be a third use of positive/destructive on
              one chart. */}
          <g fill="var(--muted-foreground)" opacity={0.35}>
            {bars.volume.map((v, i) =>
              v == null || volMax <= 0 ? null : (
                <rect
                  key={`vol-${bars.dates[i]}`}
                  x={xFor(i) - bodyWidth / 2}
                  y={volY(v)}
                  width={bodyWidth}
                  height={Math.max(0.5, VOL_TOP + VOL_H - volY(v))}
                />
              ),
            )}
          </g>
          <line
            x1={PAD.left}
            x2={WIDTH - PAD.right}
            y1={VOL_TOP + VOL_H}
            y2={VOL_TOP + VOL_H}
            stroke="var(--border)"
            strokeWidth={1}
          />
          <text
            x={WIDTH - PAD.right + 6}
            y={VOL_TOP + VOL_H / 2}
            dominantBaseline="middle"
            fontSize={9}
            fill="var(--muted-foreground)"
          >
            vol
          </text>

          {xTicks.map((i) => (
            <g key={`x-${bars.dates[i]}`}>
              <line
                x1={xFor(i)}
                x2={xFor(i)}
                y1={PAD.top}
                y2={VOL_TOP + VOL_H}
                stroke="var(--border)"
                strokeWidth={1}
                opacity={0.5}
              />
              <text
                x={xFor(i)}
                y={HEIGHT - 6}
                textAnchor="middle"
                fontSize={10}
                fill="var(--muted-foreground)"
              >
                {bars.dates[i].slice(5, 7) === '01'
                  ? bars.dates[i].slice(0, 4)
                  : monthOf(bars.dates[i])}
              </text>
            </g>
          ))}

          {hover != null && (
            <g>
              <line
                x1={xFor(hover)}
                x2={xFor(hover)}
                y1={PAD.top}
                y2={VOL_TOP + VOL_H}
                stroke="var(--muted-foreground)"
                strokeWidth={1}
                strokeDasharray="3 3"
                opacity={0.7}
              />
              {bars.close[hover] != null && (
                <>
                  <line
                    x1={PAD.left}
                    x2={WIDTH - PAD.right}
                    y1={priceY(bars.close[hover] as number)}
                    y2={priceY(bars.close[hover] as number)}
                    stroke="var(--muted-foreground)"
                    strokeWidth={1}
                    strokeDasharray="3 3"
                    opacity={0.45}
                  />
                  <circle
                    cx={xFor(hover)}
                    cy={priceY(bars.close[hover] as number)}
                    r={2.5}
                    fill="var(--foreground)"
                  />
                  <rect
                    x={WIDTH - PAD.right + 2}
                    y={priceY(bars.close[hover] as number) - 8}
                    width={PAD.right - 4}
                    height={16}
                    rx={3}
                    fill="var(--foreground)"
                  />
                  <text
                    x={WIDTH - PAD.right + 6}
                    y={priceY(bars.close[hover] as number)}
                    dominantBaseline="middle"
                    fontSize={10}
                    fill="var(--background)"
                  >
                    {formatPrice(bars.close[hover])}
                  </text>
                </>
              )}
            </g>
          )}
        </svg>

        {/* HTML rather than SVG <text>: multi-line layout, the project's own
            type tokens, and no manual text measurement to size a background
            box. Positioned as a percentage of the container width, which
            tracks the scaled viewBox without a ResizeObserver. */}
        {hovered && (
          <div
            className="pointer-events-none absolute top-2 z-10 w-44 rounded-lg border border-border bg-popover/95 px-3 py-2 text-[11px] tnum shadow-[0_1px_2px_0_rgb(0_0_0_/_0.18)]"
            style={{
              left: `${(xFor(hover as number) / WIDTH) * 100}%`,
              transform:
                (xFor(hover as number) / WIDTH) > 0.6 ? 'translateX(-100%)' : 'translateX(8px)',
            }}
          >
            <p className="font-medium mb-1">
              {humanDate(hovered.date)}
              {hovered.partial && (
                <span className="text-muted-foreground font-normal"> · in progress</span>
              )}
            </p>
            <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-muted-foreground">
              <dt>Open</dt>
              <dd className="text-right text-foreground">{formatPrice(hovered.open)}</dd>
              <dt>High</dt>
              <dd className="text-right text-foreground">{formatPrice(hovered.high)}</dd>
              <dt>Low</dt>
              <dd className="text-right text-foreground">{formatPrice(hovered.low)}</dd>
              <dt>Close</dt>
              <dd className="text-right text-foreground">{formatPrice(hovered.close)}</dd>
              <dt>Vol</dt>
              <dd className="text-right text-foreground">{compactVolume(hovered.volume)}</dd>
            </dl>
            <div className="mt-1 pt-1 border-t border-border grid grid-cols-2 gap-x-2 gap-y-0.5 text-muted-foreground">
              <dt>55d high</dt>
              <dd className="text-right text-foreground">{formatPrice(hovered.high55)}</dd>
              <dt>20d low</dt>
              <dd className="text-right text-foreground">{formatPrice(hovered.low20)}</dd>
            </div>
            {atrsToExit != null && (
              <p className="mt-1 text-muted-foreground">
                {atrsToExit >= 0 ? formatPrice(atrsToExit) : formatPrice(-atrsToExit)}{' '}
                {atrsToExit >= 0 ? 'above' : 'below'} the exit level
              </p>
            )}
            {(hovered.entry || hovered.exit) && (
              <p
                className={
                  hovered.exit ? 'mt-1 text-destructive font-medium' : 'mt-1 font-medium'
                }
                style={hovered.entry && !hovered.exit ? { color: 'var(--chart-2)' } : undefined}
              >
                {hovered.exit ? 'Exit signal' : 'Entry signal'}
              </p>
            )}
          </div>
        )}
      </div>

      {/* Announced as the keyboard steps through bars, which the static
          aria-label on the SVG can't do. */}
      <div className="sr-only" aria-live="polite">
        {hovered
          ? `${humanDate(hovered.date)}: close ${formatPrice(hovered.close)}, ` +
            `55-day level ${formatPrice(hovered.high55)}, 20-day level ${formatPrice(hovered.low20)}` +
            (hovered.entry ? ', entry signal' : '') +
            (hovered.exit ? ', exit signal' : '')
          : ''}
      </div>

      <figcaption className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <svg width={12} height={4} aria-hidden="true" className="shrink-0">
            <line x1={0} y1={2} x2={12} y2={2} stroke="var(--chart-2)" strokeWidth={2} />
          </svg>
          55-day breakout level
        </span>
        <span className="inline-flex items-center gap-1.5">
          <svg width={12} height={4} aria-hidden="true" className="shrink-0">
            <line
              x1={0}
              y1={2}
              x2={12}
              y2={2}
              stroke="var(--chart-2)"
              strokeWidth={2}
              strokeDasharray="4 3"
            />
          </svg>
          20-day exit level
        </span>
        <span className="inline-flex items-center gap-1.5">
          <svg width={8} height={8} aria-hidden="true" className="shrink-0">
            <path d="M0,8 L8,8 L4,1 Z" fill="var(--chart-2)" />
          </svg>
          Entry fired
        </span>
        <span className="inline-flex items-center gap-1.5">
          <svg width={8} height={8} aria-hidden="true" className="shrink-0">
            <path d="M0,0 L8,0 L4,7 Z" fill="var(--destructive)" />
          </svg>
          Exit fired
        </span>
        <span>{drawCandles ? 'Daily candles' : 'Daily closes'}</span>
      </figcaption>
    </figure>
  )
}
