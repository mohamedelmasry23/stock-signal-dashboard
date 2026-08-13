import { AlertCircle } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { AccountPanel } from './components/AccountPanel'
import { BacktestPanel } from './components/BacktestPanel'
import { EntryCandidates } from './components/EntryCandidates'
import { ExitAlert } from './components/ExitAlert'
import { Header } from './components/Header'
import { HoldingsPanel } from './components/HoldingsPanel'
import {
  ApiError,
  api,
  type Account,
  type AccountPatch,
  type Backtest,
  type EntryCandidate,
  type Holding,
  type Status,
} from './lib/api'

export default function App() {
  const [status, setStatus] = useState<Status | null>(null)
  const [account, setAccount] = useState<Account | null>(null)
  const [backtest, setBacktest] = useState<Backtest | null>(null)
  const [holdings, setHoldings] = useState<Holding[]>([])
  const [candidates, setCandidates] = useState<EntryCandidate[]>([])

  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [fatalError, setFatalError] = useState<string | null>(null)
  const [addError, setAddError] = useState<string | null>(null)
  const [busyTicker, setBusyTicker] = useState<string | null>(null)

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true)
    try {
      // One round trip rather than three sequential ones; a failure in any
      // of them is a failure of the whole view, so Promise.all is right here.
      // live=true pulls current quotes from Yahoo for held tickers, which
      // drive each holding's day-change and value (and therefore equity).
      // Signals stay end-of-day. The backend degrades to stored closes if
      // Yahoo is unreachable, so this never blocks the dashboard loading.
      const [s, a, h, c, bt] = await Promise.all([
        api.status(),
        api.account(true),
        api.holdings(true),
        api.entryCandidates(10),
        api.backtest(),
      ])
      setStatus(s)
      setAccount(a)
      setBacktest(bt)
      setHoldings(h)
      setCandidates(c)
      setFatalError(null)
    } catch (e) {
      setFatalError(e instanceof ApiError ? e.message : 'Something went wrong loading the dashboard.')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const addHolding = useCallback(
    async (ticker: string, shares: number | null) => {
      setAddError(null)
      setBusyTicker(ticker)
      try {
        await api.addHolding(ticker, shares)
        await load(true)
      } catch (e) {
        setAddError(e instanceof ApiError ? e.message : 'Could not add that ticker.')
        throw e // let the form know not to clear its fields
      } finally {
        setBusyTicker(null)
      }
    },
    [load],
  )

  const removeHolding = useCallback(
    async (ticker: string) => {
      setBusyTicker(ticker)
      try {
        await api.removeHolding(ticker)
        await load(true)
      } catch (e) {
        setAddError(e instanceof ApiError ? e.message : 'Could not remove that holding.')
      } finally {
        setBusyTicker(null)
      }
    },
    [load],
  )

  const updateAccount = useCallback(
    async (patch: AccountPatch) => {
      // Reload everything rather than just the account: equity feeds every
      // suggested_shares figure the API returns, so the holdings and
      // candidate panels are stale the moment this changes.
      await api.updateAccount(patch)
      await load(true)
    },
    [load],
  )

  const updateShares = useCallback(
    async (ticker: string, shares: number | null) => {
      setBusyTicker(ticker)
      try {
        await api.updateShares(ticker, shares)
        await load(true)
      } catch (e) {
        setAddError(e instanceof ApiError ? e.message : 'Could not update shares.')
      } finally {
        setBusyTicker(null)
      }
    },
    [load],
  )

  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-6xl px-4 sm:px-6 py-8">
        <Header
          status={status}
          loading={loading}
          onRefresh={() => void load(true)}
          refreshing={refreshing}
        />

        {fatalError ? (
          <div
            role="alert"
            className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/[0.08] px-4 py-3"
          >
            <AlertCircle size={16} className="text-destructive mt-0.5 shrink-0" />
            <div className="text-sm">
              <p className="font-medium">Couldn't load the dashboard</p>
              <p className="text-muted-foreground text-xs mt-0.5">{fatalError}</p>
              <p className="text-muted-foreground text-xs mt-2">
                Start it with{' '}
                <code className="font-mono bg-muted px-1 py-0.5 rounded">
                  uvicorn app.main:app --reload --port 8000
                </code>{' '}
                from the <code className="font-mono">backend/</code> folder.
              </p>
            </div>
          </div>
        ) : (
          <>
            <ExitAlert holdings={holdings} />
            <AccountPanel account={account} loading={loading} onUpdate={updateAccount} />
            <div className="grid gap-6 lg:grid-cols-2 items-start">
              <HoldingsPanel
                holdings={holdings}
                loading={loading}
                busyTicker={busyTicker}
                onAdd={addHolding}
                onRemove={(t) => void removeHolding(t)}
                onUpdateShares={(t, s) => void updateShares(t, s)}
                addError={addError}
              />
              <EntryCandidates
                candidates={candidates}
                loading={loading}
                onAdd={(t) => void addHolding(t, null).catch(() => {})}
                busyTicker={busyTicker}
              />
            </div>
            <BacktestPanel backtest={backtest} loading={loading} />
          </>
        )}

        <footer className="mt-8 text-[11px] text-muted-foreground leading-relaxed">
          Signals are computed once daily after the US close from 55/20-day Donchian channels and
          ATR(20). Position sizes are derived from your account equity above and update immediately
          when you change it. Not investment advice.
        </footer>
      </div>
    </div>
  )
}
