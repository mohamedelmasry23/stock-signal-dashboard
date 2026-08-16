import { useCallback, useEffect, useState } from 'react'

/**
 * Which strategy page is showing.
 *
 * A hash router in a dozen lines rather than a routing dependency: there
 * are two pages, no nested routes, no loaders and no params. The hash also
 * means a built bundle needs no server rewrite rule to survive a refresh
 * on `/rsi` — which matters, because the deploy story here is still a
 * static bundle plus the API and `vite.config.ts`'s proxy is dev-only.
 *
 * Back/forward work for free: changing `location.hash` pushes a history
 * entry, and `hashchange` fires when the user navigates through it.
 */

export type Route = 'donchian' | 'rsi'

export const ROUTE_LABELS: Record<Route, string> = {
  donchian: 'Donchian Breakout',
  rsi: 'RSI(2) Pullback',
}

function parse(hash: string): Route {
  return hash.replace(/^#\/?/, '') === 'rsi' ? 'rsi' : 'donchian'
}

export function useHashRoute(): [Route, (route: Route) => void] {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash))

  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  const navigate = useCallback((next: Route) => {
    // Setting the hash fires `hashchange`, which sets the state — so this
    // deliberately doesn't also call setRoute. Doing both would render
    // once from the click and once from the event.
    window.location.hash = next === 'donchian' ? '/' : `/${next}`
    // Landing halfway down the new page because the old one was scrolled
    // is disorienting when the two layouts look this similar.
    window.scrollTo({ top: 0 })
  }, [])

  return [route, navigate]
}
