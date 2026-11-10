import { Moon, MoonStar, RefreshCw, Sun, Sunrise } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useTheme } from '../hooks/useTheme'
import { greetingText, useGreeting, type TimeOfDay } from '../hooks/useGreeting'
import type { Status } from '../lib/api'
import { absoluteTime, isStale, relativeTime } from '../lib/format'
import { Badge, Button, Skeleton } from './ui'

// Who the greeting addresses. Comes from VITE_USER_NAME (set it in
// .env.local) rather than being hardcoded, so a fork or a deploy doesn't
// need a source edit. Unset means no name at all — a generic
// "Good Evening" is the safe default for anything public.
const USER_NAME = import.meta.env.VITE_USER_NAME?.trim() || null

// MoonStar rather than plain Moon for the evening: the theme toggle on
// the right already uses Moon, and two identical moons in one header
// reads as a duplicated control.
const greetingIcon: Record<TimeOfDay, LucideIcon> = {
  morning: Sunrise,
  afternoon: Sun,
  evening: MoonStar,
}

export function Header({
  status,
  loading,
  onRefresh,
  refreshing,
  strategyLabel = 'Donchian breakout',
  lastUpdated,
}: {
  status: Status | null
  loading: boolean
  onRefresh: () => void
  refreshing: boolean
  /** Named by the caller rather than derived from the route, so the header
   *  stays presentational and knows nothing about routing. */
  strategyLabel?: string
  /** When the signals were last computed. */
  lastUpdated?: string | null
}) {
  const { theme, toggle } = useTheme()
  const timeOfDay = useGreeting()
  const GreetingIcon = greetingIcon[timeOfDay]
  const updated = lastUpdated !== undefined ? lastUpdated : (status?.signals_last_updated ?? null)
  const stale = status ? isStale(updated) : false

  // Two independent guards on showing the name, and the ordering matters:
  // it's only shown once `status` has come back AND says this isn't a demo
  // instance. Checking `status?.demo_mode` directly would render the name
  // during the initial load — when status is still null — and then remove
  // it, flashing the owner's name on a public demo before the first
  // response lands. Defaulting to the bare greeting can't leak.
  const named = USER_NAME !== null && status !== null && !status.demo_mode

  // The greeting strings carry their own trailing punctuation, so the name
  // has to slot in *ahead* of it. Plain concatenation gives
  // "Good Evening! Alex", which reads as two disconnected fragments.
  // Splitting the punctuation off rather than hardcoding "!" means this
  // still holds if the strings in useGreeting.ts are reworded later.
  const base = greetingText[timeOfDay]
  const trailing = base.match(/[!.?]+$/)?.[0] ?? ''
  const stem = trailing ? base.slice(0, -trailing.length) : base
  const greeting = named ? `${stem}, ${USER_NAME}${trailing}` : base

  return (
    <header className="flex flex-wrap items-center justify-between gap-4 mb-6">
      <div className="flex items-center gap-3 min-w-0">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <GreetingIcon size={18} strokeWidth={2.25} />
        </div>
        <div className="min-w-0">
          <h1 className="text-base font-semibold tracking-tight leading-tight">
            {greeting}
          </h1>
          {loading ? (
            <Skeleton className="h-3 w-44 mt-1.5" />
          ) : (
            <p className="text-xs text-muted-foreground leading-tight mt-0.5">
              {strategyLabel} · signals{' '}
              <span title={absoluteTime(updated)}>{relativeTime(updated)}</span>
            </p>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2">
        {status?.demo_mode && (
          <Badge tone="outline" title="Serving seeded fake data — no real holdings or market data">
            Demo data
          </Badge>
        )}
        {!loading && stale && (
          <Badge
            tone="muted"
            title="The pipeline runs once daily after the US close. Older than that means a run was likely missed."
          >
            Stale
          </Badge>
        )}
        <Button variant="secondary" onClick={onRefresh} disabled={refreshing} aria-label="Refresh data">
          <RefreshCw size={13} className={refreshing ? 'animate-spin' : undefined} />
          Refresh
        </Button>
        <Button
          variant="ghost"
          onClick={toggle}
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
          className="px-2"
        >
          {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
        </Button>
      </div>
    </header>
  )
}
