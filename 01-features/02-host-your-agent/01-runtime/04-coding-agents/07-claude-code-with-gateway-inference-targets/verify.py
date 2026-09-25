"""
Verify model capabilities through the AgentCore Gateway inference target.

Run `python setup.py` first, then:

    python verify.py [--model mantle/anthropic.claude-sonnet-5]

Sends real Messages API requests through the gateway and checks the responses for
the signals that prove each capability worked, rather than only checking for HTTP 200:

    caching     cache_control on a large static prefix writes the cache on the first
                call (usage.cache_creation_input_tokens > 0) and reads it on an
                identical second call (usage.cache_read_input_tokens > 0)
    reasoning   extended thinking engages and consumes thinking tokens
                (usage.output_tokens_details.thinking_tokens > 0)
    multimodal  a base64 PNG influences the answer (the model names its colour)
    tool use    a tool definition survives transit and the model emits a tool_use
                block with stop_reason=tool_use

Reads the gateway endpoint and OAuth client credentials from .provision-state.json.
Needs only the Python standard library. Costs roughly six model calls, each with a
~6k-token cached prefix.
"""

import argparse
import base64
import json
import random
import ssl
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Any

# Pinned verifying TLS context: the OAuth client secret crosses the token connection.
_TLS = ssl.create_default_context()

STATE_FILE = Path(__file__).with_name(".provision-state.json")
PREFIX_TOKENS = 6000  # clears every model's cache-checkpoint minimum
# Adaptive thinking may skip a request, so the reasoning check tries several times.
REASONING_SAMPLES = 4
REASONING_PROMPT = (
    "A tank holds 240 L. Pipe A fills at 12 L/min, pipe B at 8 L/min, and a drain empties "
    "at 5 L/min. Pipe A runs alone for 4 minutes, then all three run together. Exactly how "
    "many minutes from the start until the tank is full? Show each step."
)
RETRYABLE = {429, 500, 502, 503, 529}
RETRY_ATTEMPTS = 4  # waits 2s, 4s, 8s between attempts

_results: list[tuple[str, bool, str]] = []


def report(name: str, ok: bool, detail: str) -> None:
    _results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")


# ── Gateway access ────────────────────────────────────────────────────────────


def outputs() -> dict[str, Any]:
    if not STATE_FILE.exists():
        sys.exit(f"{STATE_FILE.name} not found -- run `python setup.py` first")
    out = json.loads(STATE_FILE.read_text()).get("outputs") or {}
    missing = [
        k
        for k in ("inference_url", "token_url", "client_id", "client_secret", "scope")
        if not out.get(k)
    ]
    if missing:
        sys.exit(f"state outputs missing: {', '.join(missing)}")
    return out


def mint_token(out: dict[str, Any]) -> str:
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": out["client_id"],
            "client_secret": out["client_secret"],
            "scope": out["scope"],
        }
    ).encode()
    req = urllib.request.Request(
        out["token_url"],
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30, context=_TLS) as resp:
        return json.loads(resp.read())["access_token"]


def call(url: str, token: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    # Returns (status, body); a non-JSON body comes back as {"raw": <text>}.
    payload = {**payload, "anthropic_version": "bedrock-2023-05-31"}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    # Retry throttling, overload and transient 5xx errors with backoff.
    for attempt in range(RETRY_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=120, context=_TLS) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            if exc.code in RETRYABLE and attempt < RETRY_ATTEMPTS - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {"raw": raw[:500]}
    raise AssertionError("unreachable")


def error_text(body: dict[str, Any]) -> str:
    err = body.get("error")
    if isinstance(err, dict):
        return str(err.get("message", err))[:120]
    return str(body.get("message") or body.get("raw") or body)[:120]


# ── Fixtures ──────────────────────────────────────────────────────────────────


def cache_prefix() -> str:
    # Seeded so both caching calls send the same prefix, and salted with the time so
    # each run starts with a cold cache.
    rng = random.Random(20260828)
    words = [
        "ledger", "invoice", "tenant", "quota", "schema", "payload", "cursor", "broker",
        "shard", "replica", "token", "policy", "region", "cluster", "artifact", "digest",
        "manifest", "runtime", "gateway", "upstream", "latency", "throttle", "cache", "stream",
    ]
    lines = [f"Reference corpus for cache validation, run {int(time.time())}. Do not alter.\n"]
    # ~0.75 words per token is conservative; overshoot to clear the minimum.
    for i in range(0, int(PREFIX_TOKENS * 1.1), 12):
        lines.append(f"{i:05d} " + " ".join(rng.choice(words) for _ in range(12)))
    return "\n".join(lines)


def red_png_b64() -> str:
    # A solid-red PNG built in-process; the check asserts the model names the colour.
    w = h = 64
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


# ── Checks ────────────────────────────────────────────────────────────────────


def check_caching(url: str, token: str, model: str) -> None:
    # Cache write on a cold prefix, then cache read on the identical payload.
    payload = {
        "model": model,
        "max_tokens": 16,
        "system": [{"type": "text", "text": cache_prefix(), "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": "Reply with the single word: one"}],
    }
    status, body = call(url, token, payload)
    if status != 200:
        report("caching", False, f"HTTP {status} {error_text(body)}")
        return
    write = (body.get("usage") or {}).get("cache_creation_input_tokens") or 0
    if write <= 0:
        report("caching", False, f"no cache write (cache_creation_input_tokens={write})")
        return
    time.sleep(2)
    status, body = call(url, token, payload)  # byte-identical payload
    if status != 200:
        report("caching", False, f"read call HTTP {status} {error_text(body)}")
        return
    read = (body.get("usage") or {}).get("cache_read_input_tokens") or 0
    if read > 0:
        report("caching", True, f"write={write} tokens, read={read} tokens")
    else:
        report("caching", False, f"cache miss on identical prefix (write={write}, read={read})")


def check_reasoning(url: str, token: str, model: str, form: str) -> None:
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 3000,
        "messages": [{"role": "user", "content": REASONING_PROMPT}],
    }
    # Models with adaptive thinking reject the enabled form, and older models the reverse.
    if form == "adaptive":
        payload["thinking"] = {"type": "adaptive"}
        payload["output_config"] = {"effort": "high"}
    else:
        payload["thinking"] = {"type": "enabled", "budget_tokens": 2048}

    for attempt in range(1, REASONING_SAMPLES + 1):
        status, body = call(url, token, payload)
        if status != 200:
            report("reasoning", False, f"HTTP {status} {error_text(body)}")
            return
        tokens = ((body.get("usage") or {}).get("output_tokens_details") or {}).get(
            "thinking_tokens"
        ) or 0
        if tokens > 0:
            blocks = sum(1 for b in body.get("content", []) if b.get("type") == "thinking")
            report(
                "reasoning",
                True,
                f"engaged on attempt {attempt}/{REASONING_SAMPLES}: "
                f"thinking_tokens={tokens}, thinking blocks={blocks}",
            )
            return
    report(
        "reasoning",
        False,
        f"thinking_tokens=0 across {REASONING_SAMPLES} attempts -- accepted but never engaged",
    )


def check_multimodal(url: str, token: str, model: str) -> None:
    payload = {
        "model": model,
        "max_tokens": 64,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": red_png_b64(),
                        },
                    },
                    {
                        "type": "text",
                        "text": "Name the single dominant colour of this image in one word.",
                    },
                ],
            }
        ],
    }
    status, body = call(url, token, payload)
    if status != 200:
        report("multimodal", False, f"HTTP {status} {error_text(body)}")
        return
    text = " ".join(
        b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"
    ).lower()
    if "red" in text:
        report("multimodal", True, f"image read correctly: {text[:60]!r}")
    else:
        report("multimodal", False, f"HTTP 200 but colour not identified: {text[:60]!r}")


def check_tool_use(url: str, token: str, model: str) -> None:
    payload = {
        "model": model,
        "max_tokens": 512,
        "tools": [
            {
                "name": "read_file",
                "description": "Read a file from disk.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }
        ],
        "messages": [
            {"role": "user", "content": "Read the file /etc/hostname using the available tool."}
        ],
    }
    status, body = call(url, token, payload)
    if status != 200:
        report("tool use", False, f"HTTP {status} {error_text(body)}")
        return
    stop = body.get("stop_reason")
    blocks = sum(1 for b in body.get("content", []) if b.get("type") == "tool_use")
    if stop == "tool_use" and blocks > 0:
        report("tool use", True, f"stop_reason=tool_use, {blocks} tool_use block(s)")
    else:
        report("tool use", False, f"no tool_use (stop_reason={stop}, blocks={blocks})")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--model",
        default="mantle/anthropic.claude-sonnet-5",
        help="target-qualified model id (default: %(default)s)",
    )
    ap.add_argument(
        "--thinking-form",
        choices=["adaptive", "enabled"],
        default="adaptive",
        help="thinking request form; use enabled for models without adaptive thinking",
    )
    args = ap.parse_args()

    out = outputs()
    url = f"{out['inference_url'].rstrip('/')}/v1/messages"
    print(f"gateway : {url}")
    print(f"model   : {args.model}\n")
    token = mint_token(out)

    check_caching(url, token, args.model)
    check_reasoning(url, token, args.model, args.thinking_form)
    check_multimodal(url, token, args.model)
    check_tool_use(url, token, args.model)

    failed = [name for name, ok, _ in _results if not ok]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
