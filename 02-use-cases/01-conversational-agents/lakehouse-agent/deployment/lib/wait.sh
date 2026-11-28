# wait_with_progress <label> <timeout_hint> <cmd...> runs the command and prints the elapsed
# time every 15 s, then one done or failed line. It returns the command's exit code, so a
# script running under `set -e` still stops when the command fails.
wait_with_progress() {
    local label="$1"
    local hint="$2"
    shift 2
    local start=$SECONDS
    local next_report=15
    local elapsed
    local pid
    local rc

    "$@" &
    pid=$!

    while kill -0 "$pid" 2>/dev/null; do
        sleep 1
        elapsed=$((SECONDS - start))
        if [ "$elapsed" -ge "$next_report" ] && kill -0 "$pid" 2>/dev/null; then
            echo "⏳ $label: $elapsed s elapsed (up to $hint)…"
            next_report=$((next_report + 15))
        fi
    done

    if wait "$pid"; then rc=0; else rc=$?; fi
    elapsed=$((SECONDS - start))

    if [ "$rc" -eq 0 ]; then
        echo "✅ $label done in $elapsed s"
    else
        echo "❌ $label failed (exit $rc) after $elapsed s"
    fi
    return "$rc"
}
