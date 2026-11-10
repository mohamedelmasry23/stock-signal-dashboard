import { AlertCircle } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { AccountPanel } from './components/AccountPanel'
import { BacktestPanel } from './components/BacktestPanel'
import { EntryCandidates } from './components/EntryCandidates'
import { ExitAlert } from './components/ExitAlert'
import { Header } from './components/Header'
import { HoldingsPanel } from './components/HoldingsPanel'
import { IntradayWatch } from './components/IntradayWatch'
import { TickerDetail } from './components/TickerDetail'
import { useHashRoute } from './hooks/useHashRoute'
import {
  ApiError,
  api,
  type Account,
  type AccountPatch,
  type Backtest,
  type ChartRange,
  type EntryCandidate,
  type Holding,
  type Status,
  type Watch,
  type TickerDetail as TickerDetailData,
} from './lib/api'

export default function App() {
  const [route, navigate] = useHashRoute()

  const [status, setStatus] = useState<Status | null>(null)
  const [account, setAccount] = useState<Account | null>(null)
  const [backtest, setBacktest] = useState<Backtest | null>(null)
  const [watch, setWatch] = useState<Watch | null>(null)
  const [holdings, setHoldings] = useState<Holding[]>([])
  const [candidates, setCandidates] = useState<EntryCandidate[]>([])

  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [fatalError, setFatalError] = useState<string | null>(null)
  const [addError, setAddError] = useState<string | null>(null)
  const [busyTicker, setBusyTicker] = useState<string | null>(null)

  const [detail, setDetail] = useState<TickerDetailData | null>(null)
  const [detailRange, setDetailRange] = useState<ChartRange>('1Y')
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailRefreshing, setDetailRefreshing] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  // Which ticker the detail view is currently showing, so a range switch can
  // be told apart from opening a different ticker. See loadDetail.
  const shownTicker = useRef<string | null>(null)

  const load = useCallback(
    async (isRefresh = false) => {
      if (isRefresh) setRefreshing(true)
      try {
        // One round trip rather than several sequential ones; a failure in
        // any of them is a failure of the whole view, so Promise.all is
        // right here. live=true pulls current quotes from Yahoo for held
        // tickers, which drive each holding's day-change and value (and
        // therefore equity). Signals stay end-of-day. The backend degrades
        // to stored closes if Yahoo is unreachable, so this never blocks
        // the dashboard loading.
        const [s, a, h, c, bt, w] = await Promise.all([
          api.status(),
          api.account(true),
          api.holdings(true),
          api.entryCandidates(10),
          api.backtest(),
          // Reads a table a scheduled scan fills; no network work happens
          // here, so it costs nothing to include in the same round trip.
          api.watch(20),
        ])
        setStatus(s)
        setAccount(a)
        setHoldings(h)
        setCandidates(c)
        setBacktest(bt)
        setWatch(w)
        setFatalError(null)
      } catch (e) {
        setFatalError(
          e instanceof ApiError ? e.message : 'Something went wrong loading the dashboard.',
        )
      } finally {
        setLoading(false)
        setRefreshing(false)
      }
    },
    // No route dependency: opening a ticker leaves this callback's identity
    // alone, the effect below doesn't refire, and the dashboard's data
    // survives in memory. Coming back from a detail view is then instant
    // and costs no network.
    [],
  )

  useEffect(() => {
    void load()
  }, [load])

  // A new ticker starts at the default range rather than inheriting the last
  // one's zoom — 5Y on a fund that listed two years ago is a confusing first
  // impression.
  useEffect(() => {
    setDetailRange('1Y')
  }, [route.ticker])

  const loadDetail = useCallback(async () => {
    if (!route.ticker) return
    const ticker = route.ticker
    // Same loading/refreshing split the dashboard uses: a range switch keeps
    // the previous chart on screen, because blanking to a skeleton on every
    // button press reads as a full page reload.
    //
    // Which of the two applies is read from a ref rather than from `detail`,
    // so this callback doesn't take `detail` as a dependency — it sets
    // `detail`, and depending on it would rebuild the callback and refetch
    // in a loop.
    if (shownTicker.current === ticker) setDetailRefreshing(true)
    else setDetailLoading(true)
    shownTicker.current = ticker
    try {
      const next = await api.tickerDetail(ticker, detailRange)
      setDetail(next)
      setDetailError(null)
    } catch (e) {
      setDetailError(e instanceof ApiError ? e.message : 'Could not load that ticker.')
    } finally {
      setDetailLoading(false)
      setDetailRefreshing(false)
    }
  }, [route.ticker, detailRange])

  useEffect(() => {
    void loadDetail()
  }, [loadDetail])

  const openTicker = useCallback(
    (ticker: string) => navigate({ ticker }),
    [navigate],
  )

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
          onRefresh={() => {
            void load(true)
            void loadDetail()
          }}
          refreshing={refreshing || detailRefreshing}
          lastUpdated={status?.signals_last_updated ?? null}
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
        ) : route.ticker ? (
          <TickerDetail
            ticker={route.ticker}
            detail={detail}
            loading={detailLoading}
            error={detailError}
            range={detailRange}
            onRangeChange={setDetailRange}
            onBack={() => navigate({ ticker: null })}
            onAdd={(t) => void addHolding(t, null).catch(() => {})}
            onRemove={(t) => void removeHolding(t)}
            busy={busyTicker === route.ticker}
          />
        ) : (
          <>
            <ExitAlert holdings={holdings} onSelectTicker={openTicker} />
            <AccountPanel account={account} loading={loading} onUpdate={updateAccount} />
            <div className="grid gap-6 lg:grid-cols-2 items-start">
              <HoldingsPanel
                holdings={holdings}
                loading={loading}
                busyTicker={busyTicker}
                onAdd={addHolding}
                onRemove={(t) => void removeHolding(t)}
                onUpdateShares={(t, s) => void updateShares(t, s)}
                onSelectTicker={openTicker}
                addError={addError}
              />
              <EntryCandidates
                candidates={candidates}
                loading={loading}
                onAdd={(t) => void addHolding(t, null).catch(() => {})}
                onSelectTicker={openTicker}
                busyTicker={busyTicker}
              />
            </div>
            {/* Below the measured panels on purpose: signals first,
                then unmeasured live context, then the measurement. */}
            <IntradayWatch watch={watch} loading={loading} onSelectTicker={openTicker} />
            <BacktestPanel backtest={backtest} loading={loading} />
          </>
        )}

        <footer className="mt-8 text-[11px] text-muted-foreground leading-relaxed">
          Signals are computed once daily after the US close from 55/20-day Donchian channels and
          ATR(20). Position sizes are derived from your account equity above and update
          immediately when you change it. Not investment advice.
        </footer>
      </div>
    </div>
  )
}
