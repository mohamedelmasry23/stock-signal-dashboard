import { useCallback, useEffect, useState } from 'react'

type Theme = 'light' | 'dark'

const STORAGE_KEY = 'stock-dashboard-theme'

function initialTheme(): Theme {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'light' || saved === 'dark') return saved
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/**
 * The token palette in index.css defines its dark values on `.dark`, and
 * the `dark:` variant is pointed at that class via @custom-variant — so
 * toggling the class on <html> is what actually swaps the theme.
 */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(initialTheme)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    localStorage.setItem(STORAGE_KEY, theme)
  }, [theme])

  const toggle = useCallback(() => {
    setTheme((t) => (t === 'dark' ? 'light' : 'dark'))
  }, [])

  return { theme, toggle }
}
