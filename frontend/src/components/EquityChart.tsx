import type { EquityPoint } from '../lib/api'

/**
 * Strategy vs benchmark equity curve, as inline SVG.
 *
 * Hand-rolled rather than pulling in a charting library: this is two
 * polylines and a few gridlines, the project has deliberately stayed
 * dependency-light, and `StrengthBar` in EntryCandidates.tsx already
 * establishes the pattern. A charting dependency would be ~100kB to draw
 * two lines.
 *
 * Both series are rebased to 100 at the first bar. Comparing raw dollar
 * curves would only work if both started from the same capital, and the
 * question being asked is "which grew faster", not "which had more money".
 */

const WIDTH = 800
const HEIGHT = 260
const PAD = { top: 12, right: 12, bottom: 24, left: 44 }

interface Props {
  points: EquityPoint[]
  benchmarkLabel?: string
}

function rebase(values: (number | null)[]): (number | null)[] {
  const first = values.find((v) => v != null && v > 0)
  if (first == null) return values
  return values.map((v) => (v == null ? null : (v / first) * 100))
}

function toPath(values: (number | null)[], xFor: (i: number) => number, yFor: (v: number) => number) {
  let path = ''
  let penDown = false
  values.forEach((value, i) => {
    if (value == null) {
      penDown = false // a gap in the data breaks the line rather than
      return // drawing a straight segment across it
    }
    path += `${penDown ? 'L' : 'M'}${xFor(i).toFixed(2)},${yFor(value).toFixed(2)} `
    penDown = true
  })
  return path.trim()
}

export function EquityChart({ points, benchmarkLabel = 'SPY' }: Props) {
  if (points.length < 2) return null

  const strategy = rebase(points.map((p) => p.strategy))
  const benchmark = rebase(points.map((p) => p.benchmark))

  const all = [...strategy, ...benchmark].filter((v): v is number => v != null)
  const rawMin = Math.min(...all)
  const rawMax = Math.max(...all)
  // Pad the domain so the lines don't touch the frame, and always include
  // 100 so "flat since inception" is visible as a reference.
  const min = Math.min(rawMin, 100) * 0.96
  const max = Math.max(rawMax, 100) * 1.04

  const plotW = WIDTH - PAD.left - PAD.right
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const xFor = (i: number) => PAD.left + (i / (points.length - 1)) * plotW
  const yFor = (v: number) => PAD.top + plotH - ((v - min) / (max - min)) * plotH

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => min + f * (max - min))
  const firstDate = points[0].date.slice(0, 7)
  const lastDate = points[points.length - 1].date.slice(0, 7)

  return (
    <figure className="m-0">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="w-full h-auto"
        role="img"
        aria-label={`Strategy equity curve compared with ${benchmarkLabel}, both rebased to 100 at ${firstDate}.`}
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

        <text x={PAD.left} y={HEIGHT - 6} fontSize={10} fill="var(--muted-foreground)">
          {firstDate}
        </text>
        <text
          x={WIDTH - PAD.right}
          y={HEIGHT - 6}
          textAnchor="end"
          fontSize={10}
          fill="var(--muted-foreground)"
        >
          {lastDate}
        </text>

        <path
          d={toPath(benchmark, xFor, yFor)}
          fill="none"
          stroke="var(--chart-2)"
          strokeWidth={1.5}
          strokeLinejoin="round"
          opacity={0.75}
        />
        <path
          d={toPath(strategy, xFor, yFor)}
          fill="none"
          stroke="var(--chart-1)"
          strokeWidth={2}
          strokeLinejoin="round"
        />
      </svg>

      <figcaption className="flex items-center gap-4 mt-2 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block w-3 h-0.5 rounded" style={{ background: 'var(--chart-1)' }} />
          Strategy
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block w-3 h-0.5 rounded" style={{ background: 'var(--chart-2)' }} />
          {benchmarkLabel} buy &amp; hold
        </span>
        <span>Both rebased to 100 at start</span>
      </figcaption>
    </figure>
  )
}
