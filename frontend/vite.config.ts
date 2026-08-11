import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Proxy /api to the FastAPI backend so the browser only ever talks to
    // one origin in dev. Keeps CORS out of the picture entirely here — the
    // backend's wide-open allow_origins is for convenience, not something
    // this app relies on (see the deploy note in backend/README.md).
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
