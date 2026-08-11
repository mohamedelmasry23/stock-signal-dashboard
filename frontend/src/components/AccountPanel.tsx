import { Info, Wallet } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { Account, AccountPatch, EquitySource } from '../lib/api'
import { formatMoney } from '../lib/format'
import { Badge, Button, Card, CardHeader, Input, Skeleton, cn } from './ui'

const sourceLabel: Record<EquitySource, string> = {
  derived: 'From holdings + cash',
  override: 'Manually set',
  default: 'Placeholder',
}

const sourceHint: Record<EquitySource, string> = {
  derived: 'Market value of your holdings plus your cash balance. Updates automatically when you add holdings or import a Wealthsimple CSV.',
  override: 'You set this figure explicitly. Clear it to go back to deriving equity from holdings + cash.',
  default: "No holdings valued and no cash set, so sizing falls back to a $100k placeholder. Add holdings or a cash balance to size against your real account.",
}

export function AccountPanel({
  account,
  loading,
  onUpdate,
}: {
  account: Account | null
  loading: boolean
  onUpdate: (patch: AccountPatch) => Promise<void>
}) {
  const [cashDraft, setCashDraft] = useState('')
  const [riskDraft, setRiskDraft] = useState('')
  const [overrideDraft, setOverrideDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Re-sync drafts whenever the server figure changes, so an edit made
  // elsewhere (or a rejected value) doesn't leave stale text in the inputs.
  useEffect(() => {
    if (!account) return
    setCashDraft(account.cash_balance ? String(account.cash_balance) : '')
    setRiskDraft(String(+(account.risk_fraction * 100).toFixed(4)))
    setOverrideDraft(account.equity_override != null ? String(account.equity_override) : '')
  }, [account])

  async function save(patch: AccountPatch) {
    setSaving(true)
    setError(null)
    try {
      await onUpdate(patch)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save.')
    } finally {
      setSaving(false)
    }
  }

  function commitCash() {
    const v = cashDraft.trim() === '' ? 0 : Number(cashDraft)
    if (Number.isNaN(v) || v < 0) return setCashDraft(String(account?.cash_balance ?? ''))
    if (v !== account?.cash_balance) void save({ cash_balance: v })
  }

  function commitRisk() {
    const pct = Number(riskDraft)
    if (Number.isNaN(pct) || pct <= 0 || pct > 100) {
      return setRiskDraft(String(+((account?.risk_fraction ?? 0.01) * 100).toFixed(4)))
    }
    const frac = pct / 100
    if (Math.abs(frac - (account?.risk_fraction ?? 0)) > 1e-9) void save({ risk_fraction: frac })
  }

  function commitOverride() {
    const raw = overrideDraft.trim()
    // Empty clears the override — sent as an explicit null, which is the
    // only way to return equity to the derived figure.
    if (raw === '') {
      if (account?.equity_override != null) void save({ equity_override: null })
      return
    }
    const v = Number(raw)
    if (Number.isNaN(v) || v < 0) return setOverrideDraft(String(account?.equity_override ?? ''))
    if (v !== account?.equity_override) void save({ equity_override: v })
  }

  if (loading || !account) {
    return (
      <Card className="mb-6">
        <CardHeader title="Account" />
        <div className="px-5 py-4 space-y-3">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-4 w-full" />
        </div>
      </Card>
    )
  }

  return (
    <Card className="mb-6">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <Wallet size={14} />
            Account
          </span>
        }
        subtitle="Drives every position-size suggestion below"
        action={
          <Badge tone={account.equity_source === 'default' ? 'muted' : 'outline'}>
            {sourceLabel[account.equity_source]}
          </Badge>
        }
      />

      <div className="px-5 py-4">
        <div className="flex flex-wrap items-end gap-x-8 gap-y-4">
          <div>
            <p className="text-[11px] text-muted-foreground mb-1">Equity</p>
            <p
              className={cn(
                'text-2xl font-semibold tracking-tight tnum',
                account.equity_source === 'default' && 'text-muted-foreground',
              )}
            >
              {formatMoney(account.equity)}
            </p>
          </div>

          <div>
            <p className="text-[11px] text-muted-foreground mb-1">Risk per position</p>
            <p className="text-2xl font-semibold tracking-tight tnum">
              {formatMoney(account.risk_dollars)}
            </p>
          </div>

          <div className="text-[11px] text-muted-foreground leading-relaxed max-w-xs">
            <span className="inline-flex items-start gap-1.5">
              <Info size={12} className="mt-0.5 shrink-0" />
              {sourceHint[account.equity_source]}
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-x-6 gap-y-3 mt-5 pt-4 border-t border-border">
          <label className="flex items-center gap-2 text-xs">
            <span className="text-muted-foreground">Cash</span>
            <Input
              value={cashDraft}
              onChange={(e) => setCashDraft(e.target.value)}
              onBlur={commitCash}
              onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
              disabled={saving}
              inputMode="decimal"
              placeholder="0"
              aria-label="Cash balance"
              className="w-28 tnum"
            />
          </label>

          <label className="flex items-center gap-2 text-xs">
            <span className="text-muted-foreground">Risk %</span>
            <Input
              value={riskDraft}
              onChange={(e) => setRiskDraft(e.target.value)}
              onBlur={commitRisk}
              onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
              disabled={saving}
              inputMode="decimal"
              aria-label="Risk percent per position"
              className="w-20 tnum"
            />
          </label>

          <label className="flex items-center gap-2 text-xs">
            <span className="text-muted-foreground">Override equity</span>
            <Input
              value={overrideDraft}
              onChange={(e) => setOverrideDraft(e.target.value)}
              onBlur={commitOverride}
              onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
              disabled={saving}
              inputMode="decimal"
              placeholder="auto"
              aria-label="Equity override"
              className="w-28 tnum"
            />
          </label>

          {account.equity_override != null && (
            <Button
              variant="ghost"
              onClick={() => void save({ equity_override: null })}
              disabled={saving}
            >
              Use derived
            </Button>
          )}

          <span className="text-[11px] text-muted-foreground tnum">
            Holdings {formatMoney(account.positions_value)}
            {account.unvalued_holdings > 0 && (
              <span
                className="ml-1"
                title="These holdings have no share count, or no computed close yet, so they aren't counted toward equity."
              >
                · {account.unvalued_holdings} not valued
              </span>
            )}
          </span>

          {error && (
            <span role="alert" className="text-xs text-destructive">
              {error}
            </span>
          )}
        </div>
      </div>
    </Card>
  )
}
