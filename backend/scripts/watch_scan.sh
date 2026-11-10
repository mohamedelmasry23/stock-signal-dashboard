#!/bin/bash
#
# Wrapper for the intraday watchlist scan.
#
# Mirrors daily_pipeline.sh, for the same reason: a scheduled job that
# fails silently is worse than no scheduled job. One line per run to
# watch.log, and `tail watch.log` is enough to know.
#
# This scan is fired more often than it can usefully run. launchd reads
# the plist's hours as the Mac's LOCAL time, so the plist carries two sets
# of four daily fires aimed at 09:45/12:30/14:45/15:45 ET: one written in
# Athens time (16:45/19:30/21:45/22:45) and one in Eastern time. Whichever
# set doesn't match the Mac's current zone lands outside the session. That
# happened for real: when the Mac moved from Athens to Toronto time at the
# end of August 2026, the Athens-only schedule skipped every scan for two
# weeks. The four fires per zone also absorb the weeks when Athens and New
# York daylight-saving changes don't line up. The scan itself refuses
# outside 09:30-16:05 ET, so a fire outside the session is a no-op, not a
# failure, and is logged as "skipped".
#
# Invoked by a launchd agent (~/Library/LaunchAgents/com.<you>.stock-tracker.watch.plist)
# Safe to run by hand at any time.

set -uo pipefail

# BACKEND is resolved from this script's own location, so the plist only
# needs the script's absolute path. PYTHON can be overridden in the plist's
# EnvironmentVariables; the default is the python.org framework install.
BACKEND="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-/Library/Frameworks/Python.framework/Versions/3.14/bin/python3}"
LOG="$BACKEND/watch.log"
RAW="$BACKEND/data/watch_last_run.txt"

cd "$BACKEND" || { echo "$(date '+%F %T') FATAL cannot cd to $BACKEND" >> "$LOG"; exit 1; }

started=$(date '+%F %T %Z')
# Kept awake for the scan's duration — see daily_pipeline.sh for why.
/usr/bin/caffeinate -i -s "$PYTHON" -m app.watch > "$RAW" 2>&1
status=$?

skipped=$(grep -m1 'Outside the US session' "$RAW" || true)
done_line=$(grep -m1 '^\[watch\] Done\.' "$RAW" || true)
above=$(printf '%s' "$done_line" | sed -n 's/.*Done\. \([0-9]*\) above.*/\1/p')
fetched=$(printf '%s' "$done_line" | sed -n 's/.*, \([0-9]*\) of [0-9]* tickers fetched.*/\1/p')
asked=$(printf '%s' "$done_line" | sed -n 's/.*of \([0-9]*\) tickers fetched.*/\1/p')

if [ "$status" -ne 0 ]; then
    verdict="ERROR   scan exited $status"
elif [ -n "$skipped" ]; then
    # Not a warning. The guard refusing outside the session is the feature.
    verdict="skipped outside the session window"
elif [ -z "$done_line" ]; then
    verdict="ERROR   scan produced no completion line"
elif [ -z "$above" ] || [ -z "$fetched" ]; then
    # The lesson from the pipeline wrapper: a summary that stops parsing
    # must say so rather than logging blanks under an "ok".
    verdict="WARNING summary line did not parse — has the scan's wording changed?"
elif [ "${asked:-0}" -gt 0 ] && [ "$fetched" -lt $((asked / 2)) ]; then
    verdict="WARNING only ${fetched} of ${asked} tickers fetched — scan is half blind"
else
    verdict="ok      ${above} above, ${fetched} of ${asked} fetched"
fi

printf '%s  %s\n' "$started" "$verdict" >> "$LOG"

case "$verdict" in
    ERROR*|WARNING*)
        {
            echo "--- full output for the scan started $started ---"
            cat "$RAW"
            echo "--- end ---"
        } >> "$LOG"
        ;;
esac

exit "$status"
