import type { Quote } from './api'

/**
 * The minimum shape a holding needs for the display logic below: a
 * position's day-change and live-ness come from the quote and the closes.
 */
export interface PricedHolding {
  quote: Quote | null
  signal: { close: number | null; prev_close: number | null } | null
}

/**
 * The day's move.
 *
 * Prefers Yahoo's own `regularMarketChangePercent` when a live quote is
 * present: it's the figure quoted everywhere else, and Yahoo handles
 * halts, splits and odd sessions in ways a naive (last - prev) / prev
 * gets subtly wrong. Falls back to the stored end-of-day closes when
 * there is no quote, so the dashboard still works with Yahoo unreachable.
 *
 * Null rather than 0 when neither source can supply it — a ticker with a
 * single bar of history has an *unknown* move, and a confident "0.00%"
 * would be a claim the data doesn't support.
 */
export function dayChangePct(h: PricedHolding): number | null {
  if (h.quote?.change_percent != null) return h.quote.change_percent

  const s = h.signal
  if (s?.close == null || s.prev_close == null || s.prev_close <= 0) return null
  return ((s.close - s.prev_close) / s.prev_close) * 100
}

/** Whether this row's numbers came from a live quote rather than the last
 *  stored close. Drives the "live" hint so a stale figure is never
 *  mistaken for a current one. */
export function isLive(h: PricedHolding): boolean {
  return h.quote?.price != null
}
