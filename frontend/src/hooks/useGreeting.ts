import { useEffect, useState } from 'react'

export type TimeOfDay = 'morning' | 'afternoon' | 'evening'

/**
 * Bands are inclusive of their stated start and run to the minute before
 * the next one:
 *
 *   morning    03:00 - 11:59
 *   afternoon  12:00 - 18:59
 *   evening    19:00 - 02:59   <- wraps past midnight
 *
 * The evening band is why this isn't a plain descending if/else chain:
 * hours 19-23 and 0-2 are the same band, so it's expressed as the
 * fall-through rather than a range comparison that can't span midnight.
 *
 * Exported separately from the hook so the band logic can be checked
 * against every hour without rendering anything.
 */
export function timeOfDayFor(hour: number): TimeOfDay {
  if (hour >= 3 && hour < 12) return 'morning'
  if (hour >= 12 && hour < 19) return 'afternoon'
  return 'evening'
}

export const greetingText: Record<TimeOfDay, string> = {
  morning: 'Good Morning!',
  afternoon: 'Good Afternoon!',
  evening: 'Good Evening!',
}

/**
 * Uses the browser's local clock, which is what "time of day" should mean
 * here — unlike the signal timestamps, which are UTC and deliberately
 * carry an explicit offset.
 *
 * Re-checks every 30s so a dashboard left open overnight doesn't keep
 * saying "Good Afternoon" at 9pm. State is only set when the band
 * actually changes, so the interval doesn't cause a re-render a minute.
 */
export function useGreeting(): TimeOfDay {
  const [band, setBand] = useState<TimeOfDay>(() => timeOfDayFor(new Date().getHours()))

  useEffect(() => {
    const id = setInterval(() => {
      const current = timeOfDayFor(new Date().getHours())
      setBand((prev) => (prev === current ? prev : current))
    }, 30_000)
    return () => clearInterval(id)
  }, [])

  return band
}
