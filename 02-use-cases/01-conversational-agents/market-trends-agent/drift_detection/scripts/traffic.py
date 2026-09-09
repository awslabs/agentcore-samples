"""Generate agent traffic for the drift demo.

The sample ships evaluators/scripts/invoke.py, which runs a few named scenarios
one turn at a time. That is the right tool for checking that the agent works and
the wrong one for building a baseline: a detector needs tens of scored samples
before it can make any claim, and serial sessions make that a long wait.

This runs sessions concurrently and varies the tickers and brokers so the score
stream has some natural variation rather than being the same question repeated.

Usage:
  traffic.py --sessions 12 --concurrency 4
  traffic.py --sessions 12 --concurrency 4 --tag drifted
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

import boto3
from botocore.config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("traffic")

REGION = os.environ.get("AWS_REGION", "us-east-1")
AGENT_ROOT = Path(__file__).resolve().parent.parent.parent

BROKERS = [
    ("Priya Rao", "JP Morgan", "dividend-focused advisor for retail clients in Asia-Pacific"),
    ("Dana Reed", "Fidelity", "growth-oriented advisor for high net worth clients in North America"),
    ("Marco Silva", "UBS", "balanced-portfolio advisor for institutional clients in Europe"),
    ("Aisha Khan", "Morgan Stanley", "technology sector specialist for pension funds"),
    ("Tom Becker", "Schwab", "conservative income advisor for retirees"),
]

TICKERS = ["AAPL", "MSFT", "AMZN", "NVDA", "JPM", "GOOGL", "META", "TSLA"]

NEWS_TOPICS = [
    "semiconductor stocks",
    "bank earnings",
    "cloud computing revenue",
    "energy sector outlook",
    "consumer spending trends",
]


def resolve_agent_arn() -> str:
    from_env = os.environ.get("AGENT_RUNTIME_ARN", "")
    if from_env:
        return from_env
    arn_file = AGENT_ROOT / ".agent_arn"
    if arn_file.exists():
        arn = arn_file.read_text().strip()
        if arn:
            return arn
    raise SystemExit("Set AGENT_RUNTIME_ARN or ensure .agent_arn exists in the project root.")


def build_session(rng: random.Random) -> List[str]:
    """A three-turn conversation that exercises identity, prices, and news.

    Three turns is deliberate: it produces three TRACE-level samples and one
    SESSION-level sample, which is the ratio the detector has to cope with.
    """
    name, firm, style = rng.choice(BROKERS)
    a, b = rng.sample(TICKERS, 2)
    topic = rng.choice(NEWS_TOPICS)
    return [
        f"Hi, I'm {name} from {firm}. I'm a {style}. Please remember my profile.",
        f"Can you pull up the current price for {a} and {b} for me?",
        f"Any notable news on {topic} today?",
    ]


_print_lock = threading.Lock()


def run_session(client, agent_arn: str, index: int, tag: str, seed: int) -> Tuple[str, bool, float]:
    rng = random.Random(seed)
    prompts = build_session(rng)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    # AgentCore requires a session id of at least 33 characters.
    session_id = f"drift-{tag}-{index:03d}-{ts}-{rng.randrange(10**6):06d}".ljust(33, "x")

    started = time.time()
    ok = True
    for turn, prompt in enumerate(prompts, 1):
        try:
            resp = client.invoke_agent_runtime(
                agentRuntimeArn=agent_arn,
                runtimeSessionId=session_id,
                payload=json.dumps({"prompt": prompt}).encode("utf-8"),
            )
            resp["response"].read()
        except Exception as exc:  # noqa: BLE001 - one bad session must not stop the run
            ok = False
            with _print_lock:
                LOG.warning("session %d turn %d failed: %s", index, turn, exc)
            break

    elapsed = time.time() - started
    with _print_lock:
        LOG.info("session %3d %-7s %.0fs  %s", index, "ok" if ok else "FAILED", elapsed, session_id)
    return session_id, ok, elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=int, default=12, help="how many sessions to run")
    parser.add_argument("--concurrency", type=int, default=4, help="sessions in flight at once")
    parser.add_argument(
        "--tag",
        default="base",
        help="label embedded in session ids, so baseline and drifted traffic can be told apart",
    )
    parser.add_argument("--seed", type=int, default=None, help="seed for reproducible prompts")
    args = parser.parse_args()

    agent_arn = resolve_agent_arn()
    # Generous read timeout: a three-turn session with live browser scraping and
    # several model calls is slow, and a client-side timeout would show up as an
    # agent failure it is not.
    client = boto3.client(
        "bedrock-agentcore",
        region_name=REGION,
        config=Config(read_timeout=300, connect_timeout=20, retries={"max_attempts": 2}),
    )

    base_seed = args.seed if args.seed is not None else int(time.time())
    LOG.info(
        "Running %d sessions, concurrency %d, tag=%s against %s",
        args.sessions,
        args.concurrency,
        args.tag,
        agent_arn.split("/")[-1],
    )

    started = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(run_session, client, agent_arn, i, args.tag, base_seed + i) for i in range(1, args.sessions + 1)
        ]
        for fut in as_completed(futures):
            results.append(fut.result())

    ok = sum(1 for _, success, _ in results if success)
    total = time.time() - started
    print(
        json.dumps(
            {
                "sessions": args.sessions,
                "succeeded": ok,
                "failed": args.sessions - ok,
                "wallClockSeconds": round(total, 1),
                "tag": args.tag,
                "note": (
                    "Evaluation runs after the session timeout, so scores appear a few minutes after this returns."
                ),
            },
            indent=2,
        )
    )
    return 0 if ok == args.sessions else 1


if __name__ == "__main__":
    sys.exit(main())
