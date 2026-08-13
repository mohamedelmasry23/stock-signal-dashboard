// Types mirror backend/app/schemas.py. Fields the backend marks Optional
// are nullable here — notably Signal on a holding, which is null for a
// ticker added since the last pipeline run (it has no computed signal yet).

export interface Signal {
  ticker: string
  as_of_date: string | null
  close: number | null
  // Prior session's close. Null when the ticker has only one bar of
  // history, in which case the day's move is unknown rather than zero.
  prev_close: number | null
  donchian_high: number | null
  donchian_low: number | null
  atr: number | null
  stop_distance: number | null
  entry_signal: boolean
  exit_signal: boolean
  suggested_shares: number | null
  updated_at: string | null
}

/** A live quote from Yahoo. Null when the request didn't ask for one, or
 *  Yahoo didn't answer — the UI falls back to the stored end-of-day
 *  figures rather than showing nothing. */
export interface Quote {
  price: number | null
  /** Yahoo's own regularMarketChangePercent, ALREADY a percentage:
   *  0.53 means 0.53%. Do not multiply by 100. */
  change_percent: number | null
  previous_close: number | null
  /** REGULAR | CLOSED | PRE | POST */
  market_state: string | null
}

export interface Holding {
  ticker: string
  shares: number | null
  added_at: string
  signal: Signal | null
  quote: Quote | null
  /** shares x whichever price is authoritative (live if present, else the
   *  stored close). Computed server-side so the UI never has to choose. */
  market_value: number | null
}

export interface EntryCandidate {
  ticker: string
  close: number
  donchian_high: number | null
  atr: number | null
  suggested_shares: number | null
  breakout_strength: number | null
  as_of_date: string | null
}

export interface Status {
  demo_mode: boolean
  signals_last_updated: string | null
}

export type EquitySource = 'override' | 'derived' | 'default'

export interface Account {
  equity: number
  equity_source: EquitySource
  positions_value: number
  cash_balance: number
  risk_fraction: number
  equity_override: number | null
  risk_dollars: number
  unvalued_holdings: number
  /** True when equity was computed from live quotes rather than stored closes. */
  live: boolean
}

export interface EquityPoint {
  date: string
  strategy: number | null
  benchmark: number | null
}

/**
 * The metric dicts come straight from app/backtest/metrics.py. Typed as a
 * loose record on purpose: adding a metric there shouldn't require an
 * edit here, and every value is already nullable-by-design (the backend
 * returns null rather than NaN/Infinity on degenerate input).
 */
export type BacktestSummary = Record<string, number | string | null>

export interface BacktestRun {
  id: number
  label: string
  universe: string
  variant: 'faithful' | 'atr_stop'
  start_date: string
  end_date: string
  summary: BacktestSummary
  benchmark_summary: BacktestSummary | null
  equity: EquityPoint[]
}

export interface Backtest {
  faithful: BacktestRun | null
  atr_stop: BacktestRun | null
  caveats: string[]
}

export interface AccountPatch {
  cash_balance?: number
  risk_fraction?: number
  // null clears the override, returning equity to the derived figure.
  // Must be sent explicitly — omitting the key leaves it unchanged.
  equity_override?: number | null
}

export class ApiError extends Error {
  // Declared and assigned explicitly rather than as a constructor parameter
  // property: this template enables `erasableSyntaxOnly`, which rejects
  // TS-only syntax that can't be stripped without changing runtime behaviour.
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    // fetch only rejects on network-level failure — almost always the
    // backend not running. Say that rather than surfacing "Failed to fetch".
    throw new ApiError('Cannot reach the API. Is the backend running on port 8000?', 0)
  }

  if (!res.ok) {
    // In dev the Vite proxy sits in front of the API, so a stopped backend
    // surfaces as a real 502/503/504 response rather than a failed fetch —
    // the catch above never fires and the user would just see "Bad
    // Gateway". Map the gateway codes onto the same explanation.
    if (res.status === 502 || res.status === 503 || res.status === 504) {
      throw new ApiError('Cannot reach the API. Is the backend running on port 8000?', res.status)
    }

    // FastAPI puts the useful message in `detail` (e.g. "AAPL is already
    // in holdings"); fall back to the status line if the body isn't JSON.
    let detail = res.statusText
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      /* non-JSON error body — keep the status line */
    }
    throw new ApiError(detail, res.status)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  status: () => request<Status>('/status'),
  holdings: (live = false) => request<Holding[]>(`/holdings?live=${live}`),
  entryCandidates: (limit = 10) => request<EntryCandidate[]>(`/entry-candidates?limit=${limit}`),
  account: (live = false) => request<Account>(`/account?live=${live}`),
  backtest: () => request<Backtest>('/backtest'),

  updateAccount: (patch: AccountPatch) =>
    request<Account>('/account', { method: 'PATCH', body: JSON.stringify(patch) }),

  addHolding: (ticker: string, shares: number | null) =>
    request<Holding>('/holdings', {
      method: 'POST',
      body: JSON.stringify({ ticker, shares }),
    }),

  updateShares: (ticker: string, shares: number | null) =>
    request<Holding>(`/holdings/${encodeURIComponent(ticker)}`, {
      method: 'PATCH',
      body: JSON.stringify({ shares }),
    }),

  removeHolding: (ticker: string) =>
    request<void>(`/holdings/${encodeURIComponent(ticker)}`, { method: 'DELETE' }),
}
