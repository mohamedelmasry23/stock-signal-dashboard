# Deploying the public demo

The showcase deploy serves **`demo.db` only** — fake tickers, fake
holdings, no market data and no real positions. Your real instance stays
on your machine. Nothing here needs to touch `real.db`, and it shouldn't:
the demo exists so people can click around, not so your portfolio is on
the internet.

## The settings that matter

| Variable | Demo value | Why |
|---|---|---|
| `DEMO_MODE` | `true` | serves `demo.db`; the UI shows a "Demo data" badge and suppresses the greeting name |
| `READ_ONLY` | `true` | every write returns 403, so no visitor can change what others see |
| `ALLOWED_ORIGINS` | your frontend URL | replaces the localhost default; no wildcard |

The API prints its posture on the first line of the log:

```
[api] database: demo (fake data) | writes: BLOCKED (READ_ONLY=true)
[api] CORS allow_origins: ['https://your-frontend.example.com']
```

**Check that line after deploying.** All three settings are environment
variables, so a misconfigured instance looks exactly like a correct one
until someone writes to it.

## Why read-only rather than an API key

A key shipped in a frontend bundle isn't secret — anyone can read it out
of the JavaScript. For a public demo the honest control is `READ_ONLY`,
which needs no secret at all and can't be leaked. `API_KEY` is for a
*private* deploy of your real instance, where it raises the bar against
drive-by scanners.

`READ_ONLY` also wins over a valid key, so a leaked key can't make the
demo writable.

## Steps

Both parts deploy as separate services. Any host works — Render, Fly.io
and Railway all have free tiers with automatic HTTPS.

**1. Seed the demo database and commit nothing.** `data/*.db` is
git-ignored, so the demo database has to be generated on the host at
start-up rather than shipped in the repo:

```bash
DEMO_MODE=true python3 -m app.seed_demo && DEMO_MODE=true uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Use that as the service's start command. Re-seeding on every boot is a
feature here: any state a visitor somehow left behind is wiped, and the
demo always looks the way you intended.

**2. Backend service** — Python, root directory `backend`, build
`pip install -r requirements.txt`, start command as above, with
`DEMO_MODE=true`, `READ_ONLY=true` and `ALLOWED_ORIGINS` set to the
frontend URL.

**3. Frontend service** — static site, root directory `frontend`, build
`npm ci && npm run build`, publish directory `dist`. Leave
`VITE_USER_NAME` unset.

**4. Point the frontend at the API.** `vite.config.ts` proxies `/api` to
localhost:8000, which only applies to `npm run dev` — a built bundle has
no proxy. Either put both services behind one domain with `/api` routed to
the backend, or add a rewrite rule on the static host. Getting this wrong
shows up as the dashboard's "Cannot reach the API" message rather than a
blank page.

## Your real instance

Keep it local. If you do want it reachable:

- Set `API_KEY` to something long and random, and send it as `X-API-Key`.
- Set `ALLOWED_ORIGINS` to exactly your frontend origin.
- Set `DATABASE_URL` to a path on a **persistent disk** — most hosts have
  ephemeral filesystems, so a default SQLite file is wiped on every
  restart, taking your holdings and account settings with it.
- Don't put the API key in a public frontend bundle. See above.
- The pipeline needs a scheduler (cron on a VPS, or the host's scheduled
  jobs) running `python3 -m app.pipeline` once daily after the US close.
  See the cron section in `backend/README.md`.
