# Stock Signal Dashboard — Frontend

React + TypeScript + Tailwind v4 dashboard for the signal API in
`../backend`. Read-only view of your holdings' exit signals plus the
strongest entry candidates, with add/remove/edit for holdings.

## Run

The frontend needs the API running alongside it — two terminals:

```bash
cd backend && uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

Then open `http://localhost:5173`.

Vite proxies `/api` to `localhost:8000` (see `vite.config.ts`), so the
browser only ever talks to one origin in dev and CORS never comes into
play. If the API isn't running, the dashboard says so explicitly rather
than rendering an empty shell.

To point at the demo database instead, start the backend with
`DEMO_MODE=true` — the header picks up the flag from `/api/status` and
shows a "Demo data" badge.

## Layout

- **Header** — a time-of-day greeting with a matching icon, on the
  browser's local clock: morning 03:00–11:59 (sunrise), afternoon
  12:00–18:59 (sun), evening 19:00–02:59 (crescent moon). The evening band
  wraps midnight, so `timeOfDayFor()` in `src/hooks/useGreeting.ts`
  expresses it as the fall-through rather than a range comparison. The
  band is re-checked every 30s so a dashboard left open overnight doesn't
  keep saying "Good Afternoon" at 9pm. The name is the `USER_NAME`
  constant in `Header.tsx` — change it before any public deploy, since a
  demo shouldn't greet visitors by the owner's name.

- **Exit alert** — a banner above everything when any held ticker has
  closed below its 20-day Donchian low. Hidden entirely when there's
  nothing to report; a permanent "0 exit signals" would train you to
  ignore it.
- **Account** — the equity figure driving every position-size suggestion,
  with its source labelled (derived from holdings + cash, manually
  overridden, or the fresh-install placeholder). Editing cash, risk %, or
  the override takes effect immediately: the API computes sizing on read,
  so no pipeline re-run is needed. Clearing the override field (or the
  "Use derived" button) sends an explicit null, which is the only way back
  to the derived figure.
- **Your Holdings** — exit-flagged positions sort to the top with a red
  treatment. Each row shows the close, the exit trigger level, and how
  far above that trigger the position currently sits. Shares are
  editable inline (Enter commits, Escape reverts).
- **Entry Candidates** — top 10 by breakout strength, with a bar scaled
  against the strongest candidate on screen rather than a fixed ceiling
  (breakout strength has no natural upper bound).

## Theming

`src/index.css` holds the design tokens. Light values are on `:root`,
dark on `.dark`, and `@custom-variant dark (&:is(.dark *))` points
Tailwind's `dark:` variant at that class — Tailwind v4 otherwise
defaults `dark:` to `prefers-color-scheme`, which would ignore the
toggle. `useTheme` persists the choice to localStorage and falls back to
the system preference on first load.

Two notes on the token block, since it differs slightly from a stock
shadcn setup:

- Font tokens are registered under `--font-*` inside `@theme inline`,
  not `--color-*`. Registered as colours they'd generate a meaningless
  `text-font-mono` utility and no `font-mono` at all.
- `--radius-sm/md/lg` are derived from `--radius` so the `rounded-*`
  utilities resolve; `--color-radius` wouldn't produce them.

## Notes

- Timestamps from the API carry an explicit `+00:00` offset. That's
  load-bearing — see the "Timestamps" section in `../backend/README.md`
  for why, and don't strip it.
- The stale-data threshold lives in `src/lib/format.ts`
  (`STALE_AFTER_HOURS`), set to 24h to match the daily pipeline cadence.
- A holding added for a ticker the pipeline hasn't scanned yet renders
  with a "No signal yet" note rather than blank cells. Note the backend
  doesn't validate that a ticker actually exists, so a typo lands here
  too and stays until you remove it.
