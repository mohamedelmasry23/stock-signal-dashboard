import type { EquityPoint } from '../lib/api'

/**
 * Strategy vs benchmark equity curve, as inline SVG.
 *
 * Hand-rolled rather than pulling in a charting library: this is a few
 * polylines and some gridlines, the project has deliberately stayed
 * dependency-light, and `StrengthBar` in EntryCandidates.tsx already
 * establishes the pattern. A charting dependency would be ~100kB to draw
 * three lines.
 *
 * Every series is rebased to 100 at its own first bar. Comparing raw
 * dollar curves would only work if all of them started from the same
 * capital, and the question being asked is "which grew faster", not
 * "which had more money".
 *
 * The x axis is time, not array position. Each run downsamples its own
 * curve independently (see _downsample in app/backtest/store.py), so two
 * runs covering the same decade arrive with different sample dates —
 * plotting by index would stretch one against the other and draw a
 * divergence that isn't in the data.
 */

const WIDTH = 800
const HEIGHT = 260
const PAD = { top: 12, right: 12, bottom: 24, left: 44 }

// Height of the exposure strip below the price plot, and the gap above it.
const EXPOSURE_H = 22
const EXPOSURE_GAP = 8

/** How a strategy curve is drawn. Every strategy curve shares the strategy
 *  colour — they are the same system measured over different universes,
 *  not different systems — so line weight and dash carry the difference. */
export interface LineStyle {
  width?: number
  opacity?: number
  /** An SVG dasharray; `'none'` for solid. Overlays default to dashed. */
  dash?: string
}

export interface Overlay extends LineStyle {
  label: string
  points: EquityPoint[]
  /** Listed first in the legend and drawn on top of everything else —
   *  for the curve the others should be read against. */
  lead?: boolean
}

interface Props {
  points: EquityPoint[]
  benchmarkLabel?: string
  strategyLabel?: string
  /** Style for the `points` strategy curve; defaults to solid. */
  strategyStyle?: LineStyle
  /** Further strategy curves from other runs — same rules, different
   *  universe. */
  overlays?: Overlay[]
  /**
   * Draw a strip showing how much of the account was invested over time.
   *
   * Worth the space for a strategy that is mostly in cash: on the equity
   * curve alone, "held through a flat stretch" and "sat in cash" are the
   * same horizontal line, and for RSI(2) — invested under a fifth of the
   * time — that is the difference between reading the chart correctly and
   * not. Silently omitted when the run predates exposure being stored.
   */
  showExposure?: boolean
}

interface Sample {
  t: number
  value: number | null
}

interface Line {
  key: string
  label: string
  samples: Sample[]
  color: string
  width: number
  opacity: number
  dash?: string
}

function rebase(points: EquityPoint[], pick: (p: EquityPoint) => number | null): Sample[] {
  const base = points.map(pick).find((v): v is number => v != null && v > 0)
  if (base == null) return []
  return points.map((p) => {
    const value = pick(p)
    return { t: Date.parse(p.date), value: value == null ? null : (value / base) * 100 }
  })
}

function toPath(samples: Sample[], xFor: (t: number) => number, yFor: (v: number) => number) {
  let path = ''
  let penDown = false
  samples.forEach(({ t, value }) => {
    if (value == null) {
      penDown = false // a gap in the data breaks the line rather than
      return // drawing a straight segment across it
    }
    path += `${penDown ? 'L' : 'M'}${xFor(t).toFixed(2)},${yFor(value).toFixed(2)} `
    penDown = true
  })
  return path.trim()
}

function month(t: number): string {
  return new Date(t).toISOString().slice(0, 7)
}

export function EquityChart({
  points,
  benchmarkLabel = 'SPY',
  strategyLabel = 'Strategy',
  strategyStyle = {},
  overlays = [],
  showExposure = false,
}: Props) {
  if (points.length < 2) return null

  const exposure = points
    .filter((p) => p.exposure != null)
    .map((p) => ({ t: Date.parse(p.date), value: p.exposure as number }))
  const withExposure = showExposure && exposure.length > 1
  const height = withExposure ? HEIGHT + EXPOSURE_GAP + EXPOSURE_H : HEIGHT

  const overlayLine = (overlay: Overlay): Line => ({
    key: `overlay-${overlay.label}`,
    label: overlay.label,
    samples: rebase(overlay.points, (p) => p.strategy),
    color: 'var(--chart-1)',
    width: overlay.width ?? 1.5,
    opacity: overlay.opacity ?? 0.9,
    dash: overlay.dash ?? '5 3',
  })
  const drawable = overlays.filter((o) => o.points.length > 1)

  // Legend order, which is also draw order reversed (first is on top): a
  // lead curve if there is one, the headline strategy, the same strategy
  // on the other universes, then the benchmark.
  const lines: Line[] = [
    ...drawable.filter((o) => o.lead).map(overlayLine),
    {
      key: 'strategy',
      label: strategyLabel,
      samples: rebase(points, (p) => p.strategy),
      color: 'var(--chart-1)',
      width: strategyStyle.width ?? 2,
      opacity: strategyStyle.opacity ?? 1,
      dash: strategyStyle.dash,
    },
    ...drawable.filter((o) => !o.lead).map(overlayLine),
    {
      key: 'benchmark',
      label: `${benchmarkLabel} buy & hold`,
      samples: rebase(points, (p) => p.benchmark),
      color: 'var(--chart-2)',
      width: 1.5,
      opacity: 0.75,
    },
  ].filter((line) => line.samples.length > 0)

  const values = lines
    .flatMap((line) => line.samples.map((s) => s.value))
    .filter((v): v is number => v != null)
  if (values.length === 0) return null

  // Pad the domain so the lines don't touch the frame, and always include
  // 100 so "flat since inception" is visible as a reference.
  const min = Math.min(...values, 100) * 0.96
  const max = Math.max(...values, 100) * 1.04

  const times = lines.flatMap((line) => line.samples.map((s) => s.t))
  const t0 = Math.min(...times)
  const t1 = Math.max(...times)

  const plotW = WIDTH - PAD.left - PAD.right
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const xFor = (t: number) => PAD.left + (t1 === t0 ? 0 : (t - t0) / (t1 - t0)) * plotW
  const yFor = (v: number) => PAD.top + plotH - ((v - min) / (max - min)) * plotH

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => min + f * (max - min))

  // Exposure strip geometry. Capped at 1.0 rather than scaled to the
  // observed maximum: the question it answers is "how much of the account
  // was at work", and a relative scale would make 20% look like 100% on a
  // strategy that never goes fully invested.
  const stripTop = HEIGHT - PAD.bottom + EXPOSURE_GAP
  const expY = (v: number) => stripTop + EXPOSURE_H * (1 - Math.min(Math.max(v, 0), 1))
  const avgExposure = exposure.length
    ? exposure.reduce((sum, s) => sum + s.value, 0) / exposure.length
    : 0

  return (
    <figure className="m-0">
      <svg
        viewBox={`0 0 ${WIDTH} ${height}`}
        className="w-full h-auto"
        role="img"
        aria-label={
          `Equity curves from ${month(t0)} to ${month(t1)}, each rebased to 100 at the start: ` +
          lines.map((line) => line.label).join(', ') +
          (withExposure
            ? `. Below, the share of the account invested over time, averaging ${Math.round(avgExposure * 100)} percent.`
            : '.')
        }
      >
        {ticks.map((value) => (
          <g key={value}>
            <line
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={yFor(value)}
              y2={yFor(value)}
              stroke="var(--border)"
              strokeWidth={1}
            />
            <text
              x={PAD.left - 8}
              y={yFor(value)}
              textAnchor="end"
              dominantBaseline="middle"
              fontSize={10}
              fill="var(--muted-foreground)"
            >
              {Math.round(value)}
            </text>
          </g>
        ))}

        <text x={PAD.left} y={height - 6} fontSize={10} fill="var(--muted-foreground)">
          {month(t0)}
        </text>
        <text
          x={WIDTH - PAD.right}
          y={height - 6}
          textAnchor="end"
          fontSize={10}
          fill="var(--muted-foreground)"
        >
          {month(t1)}
        </text>

        {withExposure && (
          <g>
            {/* Baseline first, so a stretch at zero exposure still reads as
                a deliberate "in cash" rather than as missing data. */}
            <line
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={stripTop + EXPOSURE_H}
              y2={stripTop + EXPOSURE_H}
              stroke="var(--border)"
              strokeWidth={1}
            />
            <path
              d={
                `M${xFor(exposure[0].t).toFixed(2)},${(stripTop + EXPOSURE_H).toFixed(2)} ` +
                exposure
                  .map((s) => `L${xFor(s.t).toFixed(2)},${expY(s.value).toFixed(2)}`)
                  .join(' ') +
                ` L${xFor(exposure[exposure.length - 1].t).toFixed(2)},${(stripTop + EXPOSURE_H).toFixed(2)} Z`
              }
              fill="var(--chart-1)"
              opacity={0.28}
              stroke="none"
            />
            <text
              x={PAD.left - 8}
              y={stripTop + EXPOSURE_H / 2}
              textAnchor="end"
              dominantBaseline="middle"
              fontSize={9}
              fill="var(--muted-foreground)"
            >
              in mkt
            </text>
          </g>
        )}

        {/* Reversed so the headline strategy is drawn last and sits on top
            of the curves it's being compared against. */}
        {[...lines].reverse().map((line) => (
          <path
            key={line.key}
            d={toPath(line.samples, xFor, yFor)}
            fill="none"
            stroke={line.color}
            strokeWidth={line.width}
            strokeDasharray={line.dash}
            strokeLinejoin="round"
            opacity={line.opacity}
          />
        ))}
      </svg>

      <figcaption className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-[11px] text-muted-foreground">
        {lines.map((line) => (
          <span key={line.key} className="inline-flex items-center gap-1.5">
            <svg width={16} height={4} aria-hidden="true" className="shrink-0">
              <line
                x1={0}
                y1={2}
                x2={16}
                y2={2}
                stroke={line.color}
                strokeWidth={Math.max(line.width, 2)}
                strokeDasharray={line.dash}
                opacity={line.opacity}
              />
            </svg>
            {line.label}
          </span>
        ))}
        {withExposure && (
          <span className="inline-flex items-center gap-1.5">
            <span
              className="inline-block w-3 h-2 rounded-[2px] shrink-0"
              style={{ background: 'var(--chart-1)', opacity: 0.28 }}
            />
            Share invested (avg {Math.round(avgExposure * 100)}%)
          </span>
        )}
        <span>All rebased to 100 at start</span>
      </figcaption>
    </figure>
  )
}
