// The pipeline runs once daily after the US close, so signals are expected
// to be up to ~24h old in normal operation. Past that, a run was probably
// missed (cron not firing, Yahoo/Wikipedia fetch failing) and the numbers
// on screen are no longer "as of last close".
const STALE_AFTER_HOURS = 24

export function formatPrice(v: number | null | undefined): string {
  if (v == null) return '—'
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function formatMoney(v: number | null | undefined, decimals = 0): string {
  if (v == null) return '—'
  return v.toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

export function formatShares(v: number | null | undefined): string {
  if (v == null) return '—'
  // Fractional amounts are normal (fractional shares, or ounces of
  // bullion), but a whole number shouldn't render as "12.00".
  return Number.isInteger(v)
    ? v.toLocaleString()
    : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/**
 * The backend serializes naive-UTC timestamps with an explicit +00:00
 * offset (see UtcDateTime in backend/app/schemas.py). That matters here:
 * without the offset, `new Date()` would read them as local time and this
 * whole staleness calculation would be silently wrong by the UTC offset.
 */
export function parseTimestamp(iso: string | null): Date | null {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d
}

export function relativeTime(iso: string | null): string {
  const d = parseTimestamp(iso)
  if (!d) return 'never'

  const mins = Math.round((Date.now() - d.getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return days === 1 ? 'yesterday' : `${days}d ago`
}

export function isStale(iso: string | null): boolean {
  const d = parseTimestamp(iso)
  if (!d) return true
  return Date.now() - d.getTime() > STALE_AFTER_HOURS * 3600_000
}

export function absoluteTime(iso: string | null): string {
  const d = parseTimestamp(iso)
  if (!d) return 'no signals computed yet'
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}
