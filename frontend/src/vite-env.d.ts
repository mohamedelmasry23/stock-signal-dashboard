/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Name used in the header greeting. Optional — falls back to a generic
   * greeting with no name. Set it in `.env.local` (git-ignored) rather
   * than hardcoding a name in source.
   *
   * Vite inlines this at BUILD time, so a deployed bundle carries
   * whatever was set when it was built, not when it runs.
   */
  readonly VITE_USER_NAME?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
