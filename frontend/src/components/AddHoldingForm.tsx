import { Plus } from 'lucide-react'
import { useState } from 'react'
import { Button, Input } from './ui'

export function AddHoldingForm({
  onAdd,
  error,
}: {
  onAdd: (ticker: string, shares: number | null) => Promise<void>
  error: string | null
}) {
  const [ticker, setTicker] = useState('')
  const [shares, setShares] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const t = ticker.trim().toUpperCase()
    if (!t || submitting) return

    const parsed = shares.trim() === '' ? null : Number(shares)
    if (parsed !== null && (Number.isNaN(parsed) || parsed < 0)) return

    setSubmitting(true)
    try {
      await onAdd(t, parsed)
      // Only clear on success, so a rejected ticker (duplicate, typo) stays
      // in the field to be corrected rather than vanishing.
      setTicker('')
      setShares('')
    } catch {
      // onAdd rethrows purely to skip the clear above; the message is already
      // on screen via the `error` prop. Swallow it here — without this catch
      // the rejection escapes the async handler as an unhandled rejection.
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="px-5 py-3 border-b border-border bg-muted/40">
      <form onSubmit={handleSubmit} className="flex flex-wrap items-center gap-2">
        <Input
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
          placeholder="Ticker (e.g. GLD)"
          aria-label="Ticker symbol"
          autoCapitalize="characters"
          autoCorrect="off"
          spellCheck={false}
          // uppercase the typed value, but not the placeholder — otherwise
          // the hint renders as a shouty "TICKER (E.G. GLD)"
          className="w-36 font-mono uppercase placeholder:normal-case"
        />
        <Input
          value={shares}
          onChange={(e) => setShares(e.target.value)}
          placeholder="Shares (optional)"
          aria-label="Number of shares"
          inputMode="decimal"
          className="w-36 tnum"
        />
        <Button type="submit" disabled={!ticker.trim() || submitting}>
          <Plus size={13} />
          Add
        </Button>
        {error && (
          <span role="alert" className="text-xs text-destructive">
            {error}
          </span>
        )}
      </form>
    </div>
  )
}
