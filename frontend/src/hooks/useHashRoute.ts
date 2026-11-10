import { useCallback, useEffect, useState } from 'react'

/**
 * Where in the app we are: the dashboard, or a ticker's detail view over it.
 *
 * A hash router in a few dozen lines rather than a routing dependency:
 * there is one page and one parameter, no nested routes and no loaders.
 * The hash also means a built bundle needs no server rewrite rule to
 * survive a refresh on `/ticker/GLD` — which matters, because the deploy
 * story here is still a static bundle plus the API and `vite.config.ts`'s
 * proxy is dev-only.
 *
 * Back/forward work for free: changing `location.hash` pushes a history
 * entry, and `hashchange` fires when the user navigates through it.
 */

export interface Route {
  /** Non-null puts the ticker detail view over the dashboard. */
  ticker: string | null
}

function parse(hash: string): Route {
  const path = hash.replace(/^#\/?/, '')
  const [head, ...rest] = path.split('/')

  if (head === 'ticker' && rest.length > 0 && rest[0]) {
    try {
      // A hand-typed or truncated hash can hold a malformed escape (`#/ticker/%`),
      // and decodeURIComponent throws URIError on it. This runs inside
      // useState's initializer, so an uncaught throw white-screens the app
      // before anything has rendered.
      return { ticker: decodeURIComponent(rest.join('/')).toUpperCase() }
    } catch {
      return { ticker: null }
    }
  }

  // Anything else, including links to pages that no longer exist, lands on
  // the dashboard.
  return { ticker: null }
}

export function useHashRoute(): [Route, (route: Route) => void] {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash))

  useEffect(() => {
    const onChange = () =>
      setRoute((prev) => {
        const next = parse(window.location.hash)
        // parse() builds a fresh object every time; returning it
        // unconditionally would change identity on every hashchange and
        // re-render everything keyed on the route for nothing.
        return prev.ticker === next.ticker ? prev : next
      })
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  const navigate = useCallback((next: Route) => {
    // Setting the hash fires `hashchange`, which sets the state — so this
    // deliberately doesn't also call setRoute. Doing both would render
    // once from the click and once from the event.
    window.location.hash = next.ticker ? `/ticker/${encodeURIComponent(next.ticker)}` : '/'
    // Landing halfway down the new view because the old one was scrolled
    // is disorienting.
    //
    // This belongs here and not in an effect keyed on the route: going
    // *back* never calls navigate(), it only fires `hashchange`, so the
    // browser's own scroll restoration returns the dashboard to where it
    // was left. An effect would fire on back too and destroy that.
    window.scrollTo({ top: 0 })
  }, [])

  return [route, navigate]
}
