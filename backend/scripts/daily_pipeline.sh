#!/bin/bash
#
# Wrapper for the scheduled daily pipeline run.
#
# Exists for one reason: a scheduled job that fails silently is worse than
# no scheduled job. The pipeline is deliberately forgiving — if the
# Wikipedia fetch dies it logs a warning and carries on with commodities
# only — which is exactly how the equity universe was silently empty for a
# day early in this project. Piping raw output to a file nobody reads
# reproduces that failure mode.
#
# So this writes ONE line per run to pipeline.log, and that line says
# plainly whether the run was healthy. `tail pipeline.log` is then enough
# to know.
#
# Invoked by ~/Library/LaunchAgents/com.example.stock-tracker.pipeline.plist
# Safe to run by hand at any time.

set -uo pipefail

# Absolute paths throughout: launchd runs with a minimal environment, no
# useful PATH and no inherited working directory.
#
# The project must NOT live under ~/Desktop, ~/Documents or ~/Downloads.
# Those are TCC-protected on macOS and a launchd agent has no access to
# them — the job dies with "Operation not permitted" before it can log
# anything at all. That is why this lives under ~/Projects.
BACKEND="$HOME/Projects/stock-signal-dashboard/backend"
PYTHON="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
LOG="$BACKEND/pipeline.log"
RAW="$BACKEND/data/pipeline_last_run.txt"

cd "$BACKEND" || { echo "$(date '+%F %T') FATAL cannot cd to $BACKEND" >> "$LOG"; exit 1; }

started=$(date '+%F %T %Z')
"$PYTHON" -m app.pipeline > "$RAW" 2>&1
status=$?

# The pipeline's own summary line, e.g.
#   [pipeline] 503 equities + 12 commodities = 515 in scan universe; 5 held; ...
scan=$(grep -m1 'in scan universe' "$RAW" || true)
done_line=$(grep -m1 '^\[pipeline\] Done\.' "$RAW" || true)
equities=$(printf '%s' "$scan" | sed -n 's/.*\] \([0-9]*\) equities.*/\1/p')
failed=$(printf '%s' "$done_line" | sed -n 's/.*, \([0-9]*\) skipped\/failed.*/\1/p')
updated=$(printf '%s' "$done_line" | sed -n 's/.*Done\. \([0-9]*\) tickers updated.*/\1/p')

if [ "$status" -ne 0 ]; then
    verdict="ERROR   pipeline exited $status"
elif [ -z "$done_line" ]; then
    verdict="ERROR   pipeline produced no completion line"
elif [ "${equities:-0}" -eq 0 ]; then
    # The silent failure this whole wrapper exists to catch: the run
    # "succeeds" but scanned no equities, so the dashboard quietly becomes
    # a commodities-only tracker.
    verdict="WARNING equity universe EMPTY — S&P fetch failed, commodities only"
elif [ "${failed:-0}" -gt 50 ]; then
    verdict="WARNING ${failed} tickers failed — unusually high"
else
    verdict="ok      ${updated} updated, ${failed:-0} failed, ${equities} equities"
fi

printf '%s  %s\n' "$started" "$verdict" >> "$LOG"

# A non-ok run also gets its full output kept alongside, so there's
# something to actually diagnose from rather than just a verdict.
case "$verdict" in
    ERROR*|WARNING*)
        {
            echo "--- full output for the run started $started ---"
            cat "$RAW"
            echo "--- end ---"
        } >> "$LOG"
        ;;
esac

exit "$status"
