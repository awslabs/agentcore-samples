#!/usr/bin/env python3
"""Parallel load test for the warm pool.

Each simulated conversation measures what a user waits for: the claim plus the
first message (TTFM, time to first message), then optional follow-up turns,
then release. A sampler thread records pool depth so you can see whether the
pool keeps up with the arrival rate.

modes
  pool     claim from the broker, invoke with the claimed ID and endpoint, release
  cold     fresh session IDs and no broker: the baseline the pool has to beat
  compare  pool phase, then cold phase, same load, side-by-side report
  suite    the simple three-scenario run: pool at capacity, cold at the same volume,
           then pool oversubscribed by --mixed-concurrency ("mixed"). 10 conversations
           per concurrent slot in each scenario (e.g. --concurrency 10 -> 10/100,
           10/100, 12/120). This is what `./deploy.sh test` runs by default.

arrival
  closed loop (default): --concurrency users, each starts a new conversation as soon
                         as the previous one ends, until --conversations are done
  open loop (--rate R):  new conversations arrive at R per second (Poisson), capped at
                         --concurrency in flight; queue_ms shows when that cap is hit

examples
  ./deploy.sh test --mode suite --concurrency 10
  ./deploy.sh test --mode suite --chart before_after.png     # same run, plus a PNG bar chart
  ./deploy.sh test --mode compare --concurrency 10 --conversations 100
  ./deploy.sh test --rate 3 --conversations 300 --hold-ms 20000 --endpoint canary

Keep the new-session rate under the account quota (25/s shared with other
traffic). Cold mode creates one new session per conversation.
"""
import argparse
import json
import math
import os
import random
import statistics
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config


def ms(seconds):
    return round(seconds * 1000)


def pct(values, p):
    """Nearest-rank percentile."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


class Colors:
    def __init__(self, enabled):
        self.on = enabled

    def _wrap(self, s, code):
        return f"\033[{code}m{s}\033[0m" if self.on else s

    def bold(self, s): return self._wrap(s, "1")
    def dim(self, s): return self._wrap(s, "2")
    def red(self, s): return self._wrap(s, "31")
    def green(self, s): return self._wrap(s, "32")
    def yellow(self, s): return self._wrap(s, "33")
    def cyan(self, s): return self._wrap(s, "36")


SPARKS = "▁▂▃▄▅▆▇█"


def resample(values, width):
    """Downsample to a fixed number of points so sparklines line up in a table column."""
    if len(values) <= width:
        return values
    step = len(values) / width
    return [values[min(len(values) - 1, int(i * step))] for i in range(width)]


def sparkline(values, width=16):
    if not values:
        return ""
    values = resample(values, width)
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    return "".join(SPARKS[int((v - lo) / span * (len(SPARKS) - 1))] for v in values)


def bar(done, total, width=20):
    filled = int(width * done / total) if total else width
    return "█" * filled + "░" * (width - filled)


def cell(text, width, align=">"):
    text = str(text)
    if len(text) > width:
        text = text[:width]
    return f"{text:{align}{width}}"


class Clients:
    def __init__(self, concurrency):
        cfg = Config(max_pool_connections=max(10, concurrency * 2), read_timeout=120,
                     retries={"mode": "standard", "max_attempts": 3})
        self.lam = boto3.client("lambda", config=cfg)
        self.ac = boto3.client("bedrock-agentcore", config=cfg)
        self.ddb = boto3.client("dynamodb", config=cfg)


def broker(cl, name, body):
    r = cl.lam.invoke(FunctionName=name, Payload=json.dumps(body).encode())
    out = json.loads(r["Payload"].read())
    if isinstance(out, dict) and "errorMessage" in out:
        raise RuntimeError(out["errorMessage"])
    return out


def pool_depth(cl, a):
    total, kwargs = 0, {}
    while True:
        resp = cl.ddb.query(TableName=a.table, IndexName="available", Select="COUNT",
                            KeyConditionExpression="#p = :p",
                            ExpressionAttributeNames={"#p": "pool"},
                            ExpressionAttributeValues={":p": {"S": f"{a.runtime_arn}#{a.endpoint}"}},
                            **kwargs)
        total += resp["Count"]
        if "LastEvaluatedKey" not in resp:
            return total
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def conversation(i, mode, a, cl, submitted_at):
    rec = {"i": i, "mode": mode, "endpoint": a.endpoint, "pooled": False, "claim_ms": 0}
    started = time.perf_counter()
    rec["queue_ms"] = ms(started - submitted_at) if submitted_at else 0
    user, sid = f"lt-{uuid.uuid4().hex[:12]}", None
    try:
        if mode == "pool":
            t = time.perf_counter()
            c = broker(cl, a.broker, {"action": "claim", "userId": user,
                                      "arn": a.runtime_arn, "endpoint": a.endpoint})
            rec["claim_ms"] = ms(time.perf_counter() - t)
            sid, rec["pooled"] = c["sessionId"], bool(c["pooled"])
        else:
            sid = f"cold-{uuid.uuid4().hex}"
        turns = []
        for k in range(a.turns):
            t = time.perf_counter()
            r = cl.ac.invoke_agent_runtime(agentRuntimeArn=a.runtime_arn, runtimeSessionId=sid,
                                           qualifier=a.endpoint,
                                           payload=json.dumps({"prompt": f"turn {k}"}).encode())
            body = json.loads(r["response"].read())
            took = ms(time.perf_counter() - t)
            turns.append(took)
            if k == 0:
                rec["first_ms"] = took
                rec["infra_ms"] = took - int(body.get("handler_ms", 0))
                rec["retries"] = r["ResponseMetadata"].get("RetryAttempts", 0)
            if a.think_ms and k < a.turns - 1:
                time.sleep(a.think_ms / 1000)
        rec["turns_ms"] = turns
        rec["ttfm_ms"] = rec["claim_ms"] + rec["first_ms"]
        rec["queued_ttfm_ms"] = rec["queue_ms"] + rec["ttfm_ms"]
        if a.hold_ms:
            time.sleep(a.hold_ms / 1000)   # keep the session IN_USE, like a real conversation
    except Exception as exc:  # noqa: BLE001 - any failure anywhere in the conversation is a recorded sample, not a crash
        rec["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        if sid:
            try:
                if mode == "pool":
                    broker(cl, a.broker, {"action": "release", "userId": user, "sessionId": sid,
                                          "arn": a.runtime_arn, "endpoint": a.endpoint})
                else:
                    cl.ac.stop_runtime_session(agentRuntimeArn=a.runtime_arn, runtimeSessionId=sid,
                                               qualifier=a.endpoint)
            except Exception as exc:  # noqa: BLE001 - cleanup best-effort, recorded rather than raised
                rec["release_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return rec


def warmup_broker(concurrency, a, cl):
    """Burn through the broker Lambda's own cold starts before the timed window opens.

    When conversations == concurrency, every claim arrives in a single burst with no later
    "warm" wave to average against: each one forces a fresh execution environment, and that
    Lambda cold start (not the pool itself) ends up inside every TTFM sample. cold mode has
    no broker hop, so it never pays this tax, which otherwise makes pool look slower than cold
    for reasons that have nothing to do with pooling. This fires concurrency throwaway calls
    with an action the handler rejects immediately after importing boto3/building the clients
    (the actual cold-start cost), so no pooled session is touched.
    """
    def ping(_):
        try:
            broker(cl, a.broker, {"action": "__warmup__", "userId": "warmup",
                                  "arn": a.runtime_arn, "endpoint": a.endpoint})
        except Exception:  # noqa: BLE001, S110 - best-effort warmup, a failed ping shouldn't abort the test
            pass
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        list(ex.map(ping, range(concurrency)))


def run_phase(exec_mode, a, cl, label=None, concurrency=None, conversations=None):
    """exec_mode controls behavior (pool: claim from the broker; cold: no broker). label is
    just the display name, so a scenario like 'mixed' can reuse pool's logic at a different
    concurrency/conversations to show what happens when demand outruns pool capacity.
    """
    label = label or exec_mode
    concurrency = concurrency or a.concurrency
    conversations = conversations or a.conversations
    if exec_mode == "pool" and not a.no_warmup:
        print(f"  warming up broker lambda ({label})", file=sys.stderr, flush=True)
        warmup_broker(concurrency, a, cl)
    depth, stop = [], threading.Event()
    t0 = time.perf_counter()

    def sampler():
        while not stop.is_set():
            try:
                depth.append((round(time.perf_counter() - t0, 1), pool_depth(cl, a)))
            except Exception:  # noqa: BLE001, S110 - a missed depth sample shouldn't abort the test
                pass
            stop.wait(a.sample_s)

    th = threading.Thread(target=sampler, daemon=True)
    if exec_mode == "pool":
        th.start()
    records, done = [], [0]
    lock = threading.Lock()

    def finished(fut):
        rec = fut.result()
        with lock:
            records.append(rec)
            done[0] += 1
            if a.quiet:
                return
            n = done[0]
            if sys.stderr.isatty():
                err = sum(1 for r in records if "error" in r)
                msg = f"\r  {label} [{bar(n, conversations)}] {n}/{conversations}"
                if err:
                    msg += f" err={err}"
                print(msg, end="\n" if n == conversations else "", file=sys.stderr, flush=True)
            elif n % max(1, conversations // 10) == 0 or n == conversations:
                print(f"  {label}: {n}/{conversations}", file=sys.stderr, flush=True)

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for i in range(conversations):
            submitted = None
            if a.rate:
                if i:
                    time.sleep(random.expovariate(a.rate))
                submitted = time.perf_counter()
            ex.submit(conversation, i, exec_mode, a, cl, submitted).add_done_callback(finished)
    wall = time.perf_counter() - t0
    stop.set()
    if exec_mode == "pool":
        th.join(timeout=5)
    return {"mode": label, "concurrency": concurrency, "conversations": conversations,
            "wall_s": round(wall, 1), "records": sorted(records, key=lambda r: r["i"]), "depth": depth}


def summarize(phase):
    recs = phase["records"]
    ok = [r for r in recs if "error" not in r]
    hits = [r for r in ok if r["pooled"]]
    misses = [r for r in ok if not r["pooled"]]
    key = "queued_ttfm_ms"
    d = [x for _, x in phase["depth"]]

    def stats(rs, k):
        v = [r[k] for r in rs if k in r]
        return {"p50": pct(v, 50), "p90": pct(v, 90), "p99": pct(v, 99),
                "mean": round(statistics.mean(v)) if v else None}

    return {
        "mode": phase["mode"], "concurrency": phase["concurrency"], "conversations": phase["conversations"],
        "n": len(recs), "ok": len(ok), "errors": len(recs) - len(ok),
        "hit_rate": round(len(hits) / len(ok), 3) if ok else None,
        "ttfm": stats(ok, key), "ttfm_hit": stats(hits, key), "ttfm_miss": stats(misses, key),
        "claim": stats(ok, "claim_ms"), "infra_first": stats(ok, "infra_ms"),
        "queue": stats(ok, "queue_ms"),
        "retried": sum(1 for r in ok if r.get("retries")),
        "throughput_cps": round(len(ok) / phase["wall_s"], 2) if phase["wall_s"] else None,
        "depth": {"min": min(d), "avg": round(statistics.mean(d), 1), "end": d[-1]} if d else None,
        "depth_series": d,
        "sample_errors": sorted({r["error"] for r in recs if "error" in r})[:5],
    }


def fmt(st):
    if not st or st["p50"] is None:
        return "-"
    return f"{st['p50']}/{st['p90']}/{st['p99']}"


TABLE_COLS = [
    ("mode", 5, "<"), ("reqs", 5, ">"), ("conc", 5, ">"), ("ok", 4, ">"), ("err", 4, ">"),
    ("hit%", 5, ">"), ("TTFM p50/p90/p99", 18, ">"), ("conv/s", 7, ">"),
]


def _hline(l, m, r):
    return l + m.join("─" * (w + 2) for _, w, _ in TABLE_COLS) + r


def _row(cells):
    return "│ " + " │ ".join(cells) + " │"


def report(summaries, a):
    c = Colors(sys.stdout.isatty() and os.environ.get("NO_COLOR") is None and not a.no_color)
    print()
    print(f"endpoint={a.endpoint}  rate={a.rate or 'closed-loop'}  turns={a.turns}  hold_ms={a.hold_ms}")
    print(c.dim("TTFM = claim + first message (+ queue wait in open loop), ms as P50/P90/P99"))
    print()
    print(c.bold(_hline("┌", "┬", "┐")))
    print(c.bold(_row([cell(h, w, al) for h, w, al in TABLE_COLS])))
    print(_hline("├", "┼", "┤"))

    for s in summaries:
        pool = s["mode"] != "cold"   # "pool" and "mixed" both claim from the broker
        hit_pct = s["hit_rate"] * 100 if s["hit_rate"] is not None and pool else None
        if hit_pct is None:
            hit_cell = cell("-", 5)
        else:
            hit_color = c.green if hit_pct >= 90 else c.yellow if hit_pct >= 70 else c.red
            hit_cell = hit_color(cell(f"{hit_pct:.0f}%", 5))
        err_cell = cell(s["errors"], 4)
        err_cell = c.red(err_cell) if s["errors"] else err_cell
        cells = [
            cell(s["mode"], 5, "<"),
            cell(s["conversations"], 5),
            cell(s["concurrency"], 5),
            cell(s["ok"], 4),
            err_cell,
            hit_cell,
            cell(fmt(s["ttfm"]), 18),
            cell(s["throughput_cps"] if s["throughput_cps"] is not None else "-", 7),
        ]
        print(_row(cells))
        for e in s["sample_errors"]:
            print("  " + c.red(f"error: {e}"))
    print(_hline("└", "┴", "┘"))

    for s in summaries:
        if s["depth"]:
            d = s["depth"]
            print(f"  {s['mode']} pool depth: {sparkline(s['depth_series'])}  "
                  f"min {d['min']} / avg {d['avg']} / end {d['end']}")

    by = {s["mode"]: s for s in summaries}
    cold = by.get("cold")
    if cold and cold["ttfm"]["p50"]:
        for name, s in by.items():
            if name == "cold" or not s["ttfm"]["p50"]:
                continue
            print()
            print(c.bold(f"{name} vs cold TTFM"))
            for p in ("p50", "p90", "p99"):
                pv, cv = s["ttfm"][p], cold["ttfm"][p]
                delta = cv - pv
                pct_delta = 100 * abs(delta) / cv if cv else 0
                verb = "faster" if delta > 0 else "slower" if delta < 0 else "even"
                colorfn = c.green if delta > 0 else c.red if delta < 0 else c.dim
                print(f"  {p.upper():>3}  {pv:>5} ms vs {cv:>5} ms   " +
                      colorfn(f"{name} {abs(delta)} ms {verb} ({pct_delta:.0f}%)"))

    for name, s in by.items():
        if name != "cold" and s["hit_rate"] is not None and s["hit_rate"] < 0.9:
            print()
            print(c.yellow(f"{name} hit rate {s['hit_rate'] * 100:.0f}% is under the 90% target: "
                            "raise minPoolSize (./deploy.sh register), raise refillConcurrency "
                            "(./deploy.sh configure), or lower the arrival rate"))


# Fixed order, not re-assigned per run: slots 1/2/3 of the validated categorical palette
# (dataviz skill), chosen so the three read correctly on every pairing, not just adjacent bars.
CHART_COLORS = {"pool": "#2a78d6", "cold": "#eb6834", "mixed": "#1baf7a"}


def render_chart(summaries, path):
    """Grouped bar chart of TTFM P50/P90/P99 per mode - the before/after story in one image."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = [s for s in summaries if s["ttfm"]["p50"] is not None]
    if not rows:
        print("no data to chart", file=sys.stderr)
        return

    percentiles, labels = ["p50", "p90", "p99"], ["P50", "P90", "P99"]
    n = len(rows)
    width = 0.8 / n
    x = list(range(len(percentiles)))
    ymax = max(s["ttfm"][p] for s in rows for p in percentiles)

    fig, ax = plt.subplots(figsize=(7, 4.5), facecolor="#fcfcfb")
    ax.set_facecolor("#fcfcfb")

    for i, s in enumerate(rows):
        values = [s["ttfm"][p] for p in percentiles]
        color = CHART_COLORS.get(s["mode"], "#4a3aa7")
        offsets = [xi + (i - (n - 1) / 2) * width for xi in x]
        bars = ax.bar(offsets, values, width=width * 0.9, color=color, label=s["mode"])
        for rect, v in zip(bars, values):
            ax.text(rect.get_x() + rect.get_width() / 2, v + ymax * 0.02, f"{v}",
                    ha="center", va="bottom", fontsize=9, color="#0b0b0b")

    ax.set_ylim(0, ymax * 1.18)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, color="#0b0b0b")
    ax.set_ylabel("time to first message (ms)", color="#52514e")
    ax.set_title("Warm pool vs cold start - time to first message (lower is better)",
                 color="#0b0b0b", fontsize=12, loc="left", pad=14)
    ax.tick_params(colors="#898781")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#c3c2b7")
    ax.yaxis.grid(True, color="#e1e0d9", linewidth=0.8)
    ax.set_axisbelow(True)
    if n > 1:
        ax.legend(frameon=False, labelcolor="#52514e")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"wrote {path}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--broker", required=True)
    ap.add_argument("--runtime-arn", required=True)
    ap.add_argument("--table", required=True)
    ap.add_argument("--endpoint", default="DEFAULT")
    ap.add_argument("--mode", choices=["pool", "cold", "compare", "suite"], default="suite")
    ap.add_argument("--conversations", type=int, default=50)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--mixed-concurrency", type=int,
                     help="concurrency for the oversubscribed phase in --mode suite (default: concurrency + 2)")
    ap.add_argument("--rate", type=float, default=0, help="open-loop arrivals per second (0 = closed loop)")
    ap.add_argument("--turns", type=int, default=1, help="messages per conversation")
    ap.add_argument("--think-ms", type=int, default=0, help="pause between turns")
    ap.add_argument("--hold-ms", type=int, default=0, help="keep the session in use after the last turn")
    ap.add_argument("--settle-s", type=int, default=60, help="pause between compare phases")
    ap.add_argument("--sample-s", type=float, default=1.0, help="pool depth sampling interval")
    ap.add_argument("--out", help="write raw records and summaries as JSON")
    ap.add_argument("--chart", help="write a before/after TTFM bar chart (PNG) to this path")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-color", action="store_true", help="disable ANSI colors in the report")
    ap.add_argument("--no-warmup", action="store_true",
                     help="skip the broker Lambda warm-up burst (shows the raw cold-start tax)")
    a = ap.parse_args()
    if a.rate > 20:
        print("warning: --rate above 20/s risks the 25 new sessions/s account quota", file=sys.stderr)

    def depth_check(concurrency, label):
        depth0 = pool_depth(cl, a)
        print(f"pool depth before {label}: {depth0}", file=sys.stderr)
        if depth0 < concurrency:
            print(f"warning: pool depth ({depth0}) is below concurrency ({concurrency}): most of the "
                  f"first wave will miss and pay claim + full cold-start on top. Run `./deploy.sh fill` "
                  f"first, or lower concurrency to {depth0}.", file=sys.stderr)

    phases = []
    if a.mode == "suite":
        # three fixed, simple scenarios at 10x conversations per concurrent slot: pool at capacity,
        # cold at the same volume (apples to apples), then pool oversubscribed ("mixed").
        mixed_c = a.mixed_concurrency or (a.concurrency + 2)
        cl = Clients(max(a.concurrency, mixed_c))
        scenarios = [
            ("pool", "pool", a.concurrency, a.concurrency * 10),
            ("cold", "cold", a.concurrency, a.concurrency * 10),
            ("mixed", "pool", mixed_c, mixed_c * 10),
        ]
        for i, (label, exec_mode, conc, conv) in enumerate(scenarios):
            if i:
                print(f"settling {a.settle_s}s so the pool refills before the next phase", file=sys.stderr)
                time.sleep(a.settle_s)
            if exec_mode == "pool":
                depth_check(conc, label)
            print(f"running {label} phase (concurrency={conc}, conversations={conv})", file=sys.stderr)
            phases.append(run_phase(exec_mode, a, cl, label=label, concurrency=conc, conversations=conv))
    else:
        cl = Clients(a.concurrency)
        if a.mode in ("pool", "compare"):
            depth_check(a.concurrency, a.mode)
        for i, mode in enumerate(["pool", "cold"] if a.mode == "compare" else [a.mode]):
            if i:
                print(f"settling {a.settle_s}s so the pool refills before the next phase", file=sys.stderr)
                time.sleep(a.settle_s)
            print(f"running {mode} phase", file=sys.stderr)
            phases.append(run_phase(mode, a, cl))

    summaries = [summarize(p) for p in phases]
    report(summaries, a)
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"args": vars(a), "summaries": summaries, "phases": phases}, f, indent=2)
        print(f"\nwrote {a.out}")
    if a.chart:
        render_chart(summaries, a.chart)


if __name__ == "__main__":
    main()
