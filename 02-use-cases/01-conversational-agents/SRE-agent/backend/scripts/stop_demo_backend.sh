#!/bin/bash
# Stop all demo backend servers

set -e

echo "🛑 Stopping SRE Agent Demo Backend..."

# Find and kill all demo server processes (k8s, logs, metrics, runbooks servers)
DEMO_PIDS=$(pgrep -f "_server\.py" || echo "")

if [ -z "$DEMO_PIDS" ]; then
    echo "ℹ️  No demo backend processes found"
    exit 0
fi

echo "🔍 Found demo backend process(es): $DEMO_PIDS"

# Show which processes we're killing
echo "📋 Processes to be stopped:"
for PID in $DEMO_PIDS; do
    PROCESS_NAME=$(ps -p "$PID" -o comm= 2>/dev/null || echo "unknown")
    echo "  - PID $PID ($PROCESS_NAME)"
done

# Graceful shutdown
for PID in $DEMO_PIDS; do
    echo "📤 Sending SIGTERM to process $PID"
    kill -TERM "$PID" 2>/dev/null || echo "⚠️  Process $PID already terminated"
done

# Wait for graceful shutdown
sleep 2

# Force kill if still running
REMAINING_PIDS=$(pgrep -f "_server\.py" || echo "")
if [ -n "$REMAINING_PIDS" ]; then
    echo "💀 Force killing remaining processes: $REMAINING_PIDS"
    for PID in $REMAINING_PIDS; do
        kill -KILL "$PID" 2>/dev/null || echo "⚠️  Process $PID already terminated"
    done
fi

echo "✅ Demo backend stopped successfully"