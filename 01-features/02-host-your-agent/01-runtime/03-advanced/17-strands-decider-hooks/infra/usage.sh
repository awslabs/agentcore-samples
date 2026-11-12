#!/usr/bin/env bash
# CPU, memory and estimated V2 cost per session, from the runtime's USAGE_LOGS
# (one record per second per session). Records can arrive a few minutes late.
# Usage: ./usage.sh [session-id]       (default: every session in the last 24 h)
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
source "${STATE_FILE}" 2>/dev/null || die "no runtime in ${REGION}: run ./deploy.sh with the same REGION"

"${PY}" - "${REGION}" "$(usage_log_group "${RUNTIME_ID}")" "${1:-}" "${MIN_MEMORY_MIB}" "${BASELINE_VCPU}" <<'EOF'
import json, sys, time
from collections import defaultdict
import boto3

region, group, only_session = sys.argv[1], sys.argv[2], sys.argv[3]
base_gb, base_vcpu = int(sys.argv[4]) / 1024, float(sys.argv[5])

# AgentCore Runtime microVM v2 list prices (aws.amazon.com/bedrock/agentcore/pricing).
CONSUMPTION = {"vcpu_h": 0.1276, "gb_h": 0.0169}
COMMITTED = {"vcpu_h": 0.0997, "gb_h": 0.0132}
# Without a resource configuration, a session pays only for the vCPU and
# memory it actively uses, at the consumption rate. With one, it pays for the
# baseline (minimumMemoryInMiB and its vCPU) for as long as the session runs,
# plus usage above the baseline second by second, all at the committed rate.

logs = boto3.client("logs", region_name=region)
kwargs = {"logGroupName": group, "startTime": int((time.time() - 86400) * 1000)}
if only_session:
    kwargs["filterPattern"] = f'"{only_session}"'
sessions = defaultdict(lambda: {"seconds": 0.0, "vcpu_h": 0.0, "gb_h": 0.0,
                                "over_vcpu_h": 0.0, "over_gb_h": 0.0, "peak_gb": 0.0, "start": None})
try:
    for page in logs.get_paginator("filter_log_events").paginate(**kwargs):
        for event in page["events"]:
            rec = json.loads(event["message"])
            sid, sec = rec["attributes"]["session.id"], float(rec["attributes"]["time_elapsed_seconds"])
            vcpu_h = rec["metrics"]["agent.runtime.vcpu.hours.used"]
            gb_h = rec["metrics"]["agent.runtime.memory.gb_hours.used"]
            hours = sec / 3600
            s = sessions[sid]
            s["seconds"] += sec; s["vcpu_h"] += vcpu_h; s["gb_h"] += gb_h
            if hours:
                s["over_vcpu_h"] += max(0.0, vcpu_h - base_vcpu * hours)
                s["over_gb_h"] += max(0.0, gb_h - base_gb * hours)
                s["peak_gb"] = max(s["peak_gb"], gb_h / hours)
            s["start"] = min(s["start"] or rec["event_timestamp"], rec["event_timestamp"])
except logs.exceptions.ResourceNotFoundException:
    sys.exit(f"no usage log group yet ({group}); run ./deploy.sh")

if not sessions:
    sys.exit("no usage records yet; they can take a few minutes to arrive")

print(f"baseline {base_gb:g} GB / {base_vcpu:g} vCPU; V2 consumption ${CONSUMPTION['vcpu_h']}/vCPU-h ${CONSUMPTION['gb_h']}/GB-h; "
      f"committed ${COMMITTED['vcpu_h']}/vCPU-h ${COMMITTED['gb_h']}/GB-h")
print(f"{'session':<38} {'start':<8} {'secs':>5} {'avg vCPU':>8} {'avg GB':>7} {'peak GB':>7} {'consumption':>12} {'committed':>10}")
total_cons = total_comm = 0.0
for sid, s in sorted(sessions.items(), key=lambda kv: kv[1]["start"]):
    hours = s["seconds"] / 3600
    consumption = s["vcpu_h"] * CONSUMPTION["vcpu_h"] + s["gb_h"] * CONSUMPTION["gb_h"]
    committed = ((base_vcpu * hours + s["over_vcpu_h"]) * COMMITTED["vcpu_h"]
                 + (base_gb * hours + s["over_gb_h"]) * COMMITTED["gb_h"])
    total_cons += consumption; total_comm += committed
    print(f"{sid:<38} {time.strftime('%H:%M:%S', time.gmtime(s['start'] / 1000)):<8} {s['seconds']:>5.0f} "
          f"{s['vcpu_h'] / hours:>8.2f} {s['gb_h'] / hours:>7.2f} {s['peak_gb']:>7.2f} "
          f"${consumption:>11.5f} ${committed:>9.5f}")
if len(sessions) > 1:
    print(f"{'total':<38} {'':<8} {'':>5} {'':>8} {'':>7} {'':>7} ${total_cons:>11.5f} ${total_comm:>9.5f}")
EOF
