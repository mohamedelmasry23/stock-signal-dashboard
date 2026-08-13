import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Vite defaults to 5173 and ignores $PORT. Honouring it lets a second
    // dev server run alongside one already holding 5173 — and because of
    // the proxy below, the port genuinely doesn't matter: the browser only
    // ever talks to this origin, so the backend's CORS allowlist is never
    // consulted in dev.
    port: process.env.PORT ? Number(process.env.PORT) : undefined,

    // Proxy /api to the FastAPI backend so the browser only ever talks to
    // one origin in dev. Note this is dev-only — a built bundle has no
    // proxy, so a deploy needs both services on one domain or a rewrite
    // rule (see DEPLOY.md).
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
