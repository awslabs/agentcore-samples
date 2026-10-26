#!/usr/bin/env python3
"""Demo console — local server for the live AgentCore workflow visualization.

Runs on your laptop with your AWS creds. Fires tickets, polls the durable-execution
stage events (s3://<bucket>/demo-progress/<ticket>.json emitted by the orchestrator),
tails per-component CloudWatch logs (prettified + ticket-scoped), parses the coding
agent's reasoning stream, and hands the frontend deep-links into the AWS console.

Usage:
    PY_BIN=/tmp/poc-venv/bin/python  python demo/serve.py        # then open http://localhost:8765
Reads deploy/config.env for ARNs / log groups / region / account.
"""
import json
import os
import re
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = {}
with open(os.path.join(ROOT, "deploy", "config.env")) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            CFG[k] = v

REGION = CFG.get("AWS_REGION", "us-east-1")
ACCOUNT = CFG.get("AWS_ACCOUNT", "")
BUCKET = CFG.get("BUCKET", "")
PROJECT = CFG.get("PROJECT", "cagent")
ORCH_ARN = CFG.get("ORCH_DURABLE_ARN", "")
MEMORY_ID = CFG.get("MEMORY_ID", "")

s3 = boto3.client("s3", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)
ac = boto3.client("bedrock-agentcore", region_name=REGION)

# component -> CloudWatch log group
RUNTIME_LG = "/aws/bedrock-agentcore/runtimes/{}-DEFAULT"
COMPONENTS = {
    "orchestrator": {"label": "Durable Orchestrator", "lg": "/aws/lambda/cagent-orchestrator-durable"},
    "coding_agent": {"label": "Coding Agent", "lg": RUNTIME_LG.format(CFG.get("RT_CAGENT_CODING_AGENT_ID", ""))},
    "sandbox_swift": {"label": "Swift Sandbox", "lg": RUNTIME_LG.format(CFG.get("RT_CAGENT_SANDBOX_SWIFT_ID", ""))},
    "sandbox": {"label": "Python Sandbox", "lg": RUNTIME_LG.format(CFG.get("RT_CAGENT_SANDBOX_ID", ""))},
    "evaluator": {"label": "Evaluator Agent", "lg": RUNTIME_LG.format(CFG.get("RT_CAGENT_EVALUATOR_ID", ""))},
}


def _console_logs_url(lg: str) -> str:
    enc = lg.replace("/", "$252F")
    return (f"https://{REGION}.console.aws.amazon.com/cloudwatch/home?region={REGION}"
            f"#logsV2:log-groups/log-group/{enc}")


def _session_id_for(tid: str) -> str:
    import hashlib
    h = hashlib.sha256(f"{PROJECT}:{tid}".encode()).hexdigest()[:32]
    return f"{PROJECT}-{h}"


# ---- AWS reads -------------------------------------------------------------
def get_state(tid: str) -> dict:
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=f"demo-progress/{tid}.json")
        return json.loads(obj["Body"].read())
    except Exception:
        return {"ticket": tid, "events": [], "current": None}


def get_ticket(tid: str) -> dict:
    """The ticket the system is working: its content (title/description/repo/runtime)
    from tickets-source, plus the run's final status/attempts from the stage events."""
    out = {"ticket": tid}
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=f"tickets-source/{tid}.json")
        t = json.loads(obj["Body"].read())
        out.update({"id": t.get("id", tid), "title": t.get("title", ""),
                    "description": t.get("description", ""),
                    "repo": t.get("repo", ""), "runtime": t.get("runtime", ""),
                    "repo_url": t.get("repo_url", "")})
    except Exception as e:
        out["error"] = f"ticket source not found: {e}"
    # overlay run status from the progress doc
    st = get_state(tid)
    by = {e["stage"]: e for e in st.get("events", [])}
    fin = by.get("finalize")
    if fin:
        out["status"] = fin.get("meta", {}).get("outcome", fin.get("status"))
        out["attempts"] = fin.get("meta", {}).get("attempts")
        out["lessons_written"] = fin.get("meta", {}).get("lessons_written")
    else:
        out["status"] = "running" if st.get("current") else "not started"
    out["current_stage"] = st.get("current")
    return out


def tail_logs(component: str, tid: str, minutes: int = 30, limit: int = 60) -> dict:
    """Per-component logs, STRICTLY scoped to one ticket's run so concurrent/back-to-back
    runs never interleave on screen. Scoping is done server-side via a CloudWatch
    filterPattern on a per-run identifier:
      - coding_agent : the ticket-tagged reasoning prefix  [AGENT|<ticket>|
      - sandboxes    : the run's unique runtimeSessionId (present in their JSON log lines)
      - orchestrator : the ticket id
    """
    c = COMPONENTS.get(component)
    if not c:
        return {"error": f"unknown component {component}"}
    lg = c["lg"]
    sid = _session_id_for(tid)
    if component in ("coding_agent", "evaluator"):
        pattern = f'"[AGENT|{tid}|"'   # both emit ticket-tagged [AGENT|<ticket>|<kind>] lines
    elif component in ("sandbox", "sandbox_swift"):
        pattern = f'"{sid}"'
    else:  # orchestrator / other
        pattern = f'"{tid}"'
    out = {"component": component, "label": c["label"], "log_group": lg,
           "console_url": _console_logs_url(lg), "scope": pattern, "lines": []}
    try:
        start = int((time.time() - minutes * 60) * 1000)
        ev = logs.filter_log_events(logGroupName=lg, startTime=start,
                                    filterPattern=pattern, limit=400)
        lines = []
        for e in ev.get("events", []):
            msg = e["message"].rstrip("\n")
            pretty = _prettify(component, msg, tid, sid)
            if pretty:
                lines.append({"ts": e["timestamp"], "text": pretty})
        out["lines"] = lines[-limit:]
    except logs.exceptions.ResourceNotFoundException:
        out["lines"] = [{"ts": 0, "text": "(log group not created yet — component idle)"}]
    except Exception as e:
        out["lines"] = [{"ts": 0, "text": f"(log read error: {e})"}]
    return out


# Noise filters: platform envelopes / health probes we don't want on a customer screen.
_NOISE = re.compile(r"Invalid HTTP request|platform\.(start|init|runtimeDone|extension)|"
                    r"\"type\":\"platform\.|RequestId:|INIT_START|Runtime Version|"
                    r"Found credentials|cedarpy not installed")


def _prettify(component: str, msg: str, tid: str, sid: str) -> str | None:
    if _NOISE.search(msg):
        return None
    # Coding-agent structured reasoning/tool lines: [AGENT|<ticket>|<kind>] body
    m = re.search(r"\[AGENT\|([^|]*)\|(\w+)\]\s*(.*)", msg)
    if m:
        line_tid, kind, body = m.group(1), m.group(2), m.group(3).replace("\\n", " ")
        if tid and line_tid and line_tid != tid:
            return None  # belongs to a different ticket's run
        icon = {"REASONING": "💭", "TOOL": "🔧", "THINKING": "🧠"}.get(kind, "•")
        return f"{icon} {body[:240]}"
    # JSON log lines from the bedrock_agentcore app / our handler.
    try:
        j = json.loads(msg)
        if isinstance(j, dict) and "message" in j:
            return f"• {j['message'][:240]}"
    except Exception:
        pass
    # InvokeAgentRuntimeCommand command echo (the test gate) — only for THIS run's ticket.
    if "command=" in msg and tid in msg:
        return "🧪 " + msg.split("command=", 1)[1][:240]
    # Generic fallback: keep only lines tied to THIS run (ticket id or session id present).
    # tail_logs already filters server-side, but this guards the rendered view too.
    if tid in msg or sid in msg:
        return msg[:240]
    return None


def get_reasoning(tid: str) -> dict:
    """Pull the coding agent's [AGENT|<ticket>|<kind>] stream, scoped to this ticket.
    The coding-agent runtime serves all tickets, so filter by the embedded ticket prefix."""
    lg = COMPONENTS["coding_agent"]["lg"]
    items = []
    try:
        start = int((time.time() - 60 * 60) * 1000)
        # Filter to this ticket's lines server-side; old-format lines (no ticket) are dropped.
        ev = logs.filter_log_events(logGroupName=lg, startTime=start,
                                    filterPattern=f'"[AGENT|{tid}|"', limit=400)
        for e in ev.get("events", []):
            m = re.search(r"\[AGENT\|([^|]*)\|(\w+)\]\s*(.*)", e["message"])
            if m and m.group(1) == tid:
                items.append({"ts": e["timestamp"], "kind": m.group(2),
                              "text": m.group(3).replace("\\n", " ")[:6000]})
    except Exception as e:
        return {"items": [], "error": str(e), "console_url": _console_logs_url(lg)}
    return {"items": items, "console_url": _console_logs_url(lg)}


# Template tickets live in S3 (tickets-source/_template-<kind>.json) — they carry the repo,
# repo_url, runtime, title and description. serve.py holds NO ticket content; each "Fire"
# reads the template, stamps a fresh unique id (so every run is clean — own /mnt/shared/<id>
# dir, no stale replay), and seeds that as the live ticket. Templates are seeded by
# deploy/05_s3files.sh and are the single source of truth for what the demo fires.
_TEMPLATE_KEY = {"feature": "tickets-source/_template-feature.json",
                 "memory": "tickets-source/_template-memory.json"}


def _new_ticket_id(kind: str, stamp: str) -> str:
    return f"RAINBOW-{'F' if kind == 'feature' else 'M'}{stamp}"


def fire_ticket(kind: str, stamp: str) -> dict:
    """Read the template ticket from S3, mint a fresh id, seed source + progress, fire it."""
    if not ORCH_ARN:
        return {"error": "ORCH_DURABLE_ARN not in config.env"}
    key = _TEMPLATE_KEY.get(kind, _TEMPLATE_KEY["feature"])
    tid = _new_ticket_id(kind, stamp)
    qualifier = ORCH_ARN.rsplit(":", 1)[-1]
    try:
        tpl = json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except Exception as e:
        return {"error": f"template {key} not found (run deploy/05_s3files.sh): {e}"}
    # seed the live ticket source (template + fresh id) so admission/hydrate find it
    try:
        ticket = {**tpl, "id": tid}
        s3.put_object(Bucket=BUCKET, Key=f"tickets-source/{tid}.json",
                      Body=json.dumps(ticket).encode(), ContentType="application/json")
        # fresh progress doc so the timeline starts clean
        s3.put_object(Bucket=BUCKET, Key=f"demo-progress/{tid}.json",
                      Body=json.dumps({"ticket": tid, "events": [
                          {"stage": "received", "status": "done", "ts": time.time(),
                           "meta": {"note": "EventBridge ticket event"}}], "current": "received"}).encode(),
                      ContentType="application/json")
    except Exception as e:
        return {"error": f"seed failed: {e}"}
    try:
        lam.invoke(FunctionName=f"cagent-orchestrator-durable:{qualifier}",
                   InvocationType="Event", Payload=json.dumps({"ticketId": tid}).encode())
        return {"fired": tid, "qualifier": qualifier}
    except Exception as e:
        return {"error": str(e)}


def memory_records(repo: str) -> dict:
    ns = f"lessons/{repo}".lower()
    try:
        r = ac.retrieve_memory_records(memoryId=MEMORY_ID, namespace=ns,
                                       searchCriteria={"searchQuery": "lessons", "topK": 10}, maxResults=10)
        recs = [(x.get("content") or {}).get("text", "") for x in r.get("memoryRecordSummaries", [])]
        return {"namespace": ns, "records": [x for x in recs if x]}
    except Exception as e:
        return {"namespace": ns, "records": [], "error": str(e)}


# ---- HTTP ------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):
            # Browser closed the connection (refresh / 3s poll superseded a slow request).
            # Harmless — the request completed fine; just don't crash the handler thread.
            pass

    def handle_one_request(self):
        # Swallow client-disconnect noise so it never dumps a traceback mid-demo.
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        tid = (q.get("ticket") or [""])[0]
        if u.path in ("/", "/index.html"):
            with open(os.path.join(os.path.dirname(__file__), "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html")
        if u.path == "/config":
            return self._send(200, {"account": ACCOUNT, "region": REGION, "bucket": BUCKET,
                                    "components": {k: {"label": v["label"],
                                                       "console_url": _console_logs_url(v["lg"])}
                                                   for k, v in COMPONENTS.items()}})
        if u.path == "/fire":
            kind = (q.get("kind") or ["feature"])[0]
            stamp = time.strftime("%m%d-%H%M%S", time.localtime())
            return self._send(200, fire_ticket(kind, stamp))
        if u.path == "/state":
            return self._send(200, get_state(tid))
        if u.path == "/ticket":
            return self._send(200, get_ticket(tid))
        if u.path == "/logs":
            return self._send(200, tail_logs((q.get("component") or ["orchestrator"])[0], tid))
        if u.path == "/reasoning":
            return self._send(200, get_reasoning(tid))
        if u.path == "/memory":
            return self._send(200, memory_records((q.get("repo") or ["rainbow"])[0]))
        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8765"))
    print(f"demo console → http://localhost:{port}  (account {ACCOUNT}, region {REGION})")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
