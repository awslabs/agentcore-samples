"""
Validate Bazaar Curation — standalone verification script.

Confirms that the Coinbase x402 Bazaar's **curation** layer is active and honored
through your AgentCore Gateway. Run after Step 1 of the README (Gateway deployed and
GATEWAY_URL in the shared .env).

What it checks (read-only — no payments, no wallet required):
  1. The Gateway exposes the three Bazaar tools (search_resources, proxy_tool_call,
     validate_endpoint), prefixed with the target name (e.g. CoinbaseBazaar___...).
  2. Curation is enabled and honored. The Bazaar returns curated resources BY DEFAULT,
     so the check does not pass a redundant curatedOnly=true; instead it verifies, from
     each result's own curation metadata:
       - the DEFAULT search (no curatedOnly) returns only curated resources — every
         result carries _meta["x402/curation"].curated == true; and
       - a curatedOnly=false search surfaces resources that omit that block entirely
         (genuinely uncurated endpoints the default view hides), proving the filter
         actually narrows results.

Why per-result metadata, not set math: search_resources returns at most 20 results per
call, sets `partialResults=true` when more match, and has no offset/cursor — each result
set is a relevance-ranked, truncated sample. Comparing two such samples by set difference
is unreliable (they can differ from ranking alone). The per-result `curated` flag is
deterministic, so the verdict uses it.

Note on counting: because of the 20-result cap and lack of pagination, the full catalog
cannot be enumerated. Endpoint counts below are the DISTINCT resources discovered across a
set of probe queries — a LOWER BOUND, not the catalog size.

Usage:
    python validate_bazaar_curation.py

Requires: GATEWAY_URL in the shared .env (00-getting-started/.env). If the Gateway uses
CUSTOM_JWT inbound auth, also CLIENT_ID / CLIENT_SECRET / TOKEN_URL (auto-detected, same
as bazaar_gateway_agent.py). NONE-auth gateways need no credentials.

Note: you may see MCP output-schema validation warnings on stderr. The Bazaar returns extra
fields (e.g. `bundleSlugs`) that its advertised _meta.x402/curation schema doesn't declare, so
strict validation rejects those responses. Such a probe is skipped (counted below) rather than
failing the run, which only makes the discovered counts a more conservative lower bound.
"""

import json
import os
import sys
from datetime import timedelta

from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient

# Shared Tutorial 00 .env (one directory up)
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(ENV_FILE, override=True)

# Gateway target name used when the target was added (agentcore add gateway-target
# --name CoinbaseBazaar ...). Gateway prefixes each tool with "<target>___".
TARGET = os.environ.get("BAZAAR_TARGET_NAME", "CoinbaseBazaar")
SEARCH_TOOL = f"{TARGET}___search_resources"

# Probe queries — diverse terms so the 20-result-per-call cap surfaces a wide sample.
# More/broader queries discover more distinct endpoints; this is a sample, not a census.
QUERIES = [
    "",
    "search",
    "data",
    "weather",
    "email",
    "file",
    "image",
    "code",
    "travel",
    "finance",
    "ai",
    "api",
    "crypto",
    "stock",
    "market",
    "map",
    "price",
    "news",
]

# Rough category buckets for a human-readable breakdown of what curation surfaces.
CATEGORIES = {
    "web search": ["search", "tavily", "exa", "serp", "google"],
    "finance/markets": ["stock", "price", "sec", "earning", "market", "defi", "kalshi", "polymarket", "messari"],
    "crypto/onchain": ["wallet", "token", "dex", "chain", "nft", "blockchain", "solana", "eth"],
    "travel": ["flight", "travel", "hotel", "seats", "tripadvisor"],
    "maps/local": ["map", "nearby", "solar", "geo", "place"],
    "enrichment/contacts": ["contact", "whitepages", "reddit", "clado", "people"],
    "dev tools": ["screenshot", "upload", "openrouter", "lens", "render"],
    "weather": ["weather", "forecast", "climate"],
}


def _extract_json(tool_result):
    """Pull the JSON payload out of a Strands ToolResult (content is a list of blocks)."""
    for block in tool_result.get("content", []) or []:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except (ValueError, TypeError):
                continue
    return {}


def _is_curated(tool):
    """True if the resource is tagged curated. Curated resources carry
    _meta["x402/curation"].curated == true; uncurated resources omit that block entirely
    (per the Bazaar schema), so absence of the flag means uncurated."""
    meta = tool.get("_meta") or {}
    curation = meta.get("x402/curation") or {}
    return curation.get("curated") is True


def search(mcp_client, query, curated_only, idx):
    """Call search_resources through the Gateway; return (tools, partialResults).

    `curated_only` is True/False to set the filter, or None to omit it (the Bazaar's
    default, which returns curated resources only).

    Returns (None, False) for a skipped probe. Some Bazaar responses fail Strands'
    output-schema validation because the server returns extra fields its advertised
    _meta.x402/curation schema doesn't declare (e.g. `bundleSlugs`). Depending on the
    installed Strands/mcp version that surfaces either as a raised exception or as an
    error ToolResult (status == "error") — both are treated as a skipped probe, so one
    bad response never tanks the run and counts stay a lower bound.
    """
    arguments = {"query": query, "limit": 20}
    if curated_only is not None:
        arguments["curatedOnly"] = curated_only
    try:
        result = mcp_client.call_tool_sync(tool_use_id=f"validate-{idx}", name=SEARCH_TOOL, arguments=arguments)
    except Exception as e:  # noqa: BLE001 — one bad response shouldn't tank the run
        print(f"  (skipped query {query!r} curatedOnly={curated_only}: {type(e).__name__})")
        return None, False
    if isinstance(result, dict) and result.get("status") == "error":
        print(f"  (skipped query {query!r} curatedOnly={curated_only}: error ToolResult)")
        return None, False
    payload = _extract_json(result)
    return payload.get("tools", []), payload.get("partialResults", False)


def discover(mcp_client, curated_only):
    """Run every probe query; return ({tool_name: (description, is_curated)}, whether any
    call reported partialResults, and how many probes were skipped)."""
    found, partial_seen, skipped = {}, False, 0
    for i, q in enumerate(QUERIES):
        tools, partial = search(mcp_client, q, curated_only, i)
        if tools is None:
            skipped += 1
            continue
        partial_seen = partial_seen or bool(partial)
        for t in tools:
            found[t["name"]] = (t.get("description", "") or "", _is_curated(t))
    return found, partial_seen, skipped


def categorize(tools):
    counts = {c: 0 for c in CATEGORIES}
    for name, (desc, _curated) in tools.items():
        hay = f"{name} {desc}".lower()
        for cat, kws in CATEGORIES.items():
            if any(kw in hay for kw in kws):
                counts[cat] += 1
    return {c: n for c, n in counts.items() if n}


def main():
    gateway_url = os.environ.get("GATEWAY_URL", "")
    if not gateway_url:
        print("ERROR: GATEWAY_URL not set in .env. Deploy the Gateway first (README Step 1).")
        sys.exit(1)

    # Gateway auth — auto-detect from .env (same logic as bazaar_gateway_agent.py)
    gateway_headers = {}
    client_id = os.environ.get("CLIENT_ID")
    client_secret = os.environ.get("CLIENT_SECRET")
    token_url = os.environ.get("TOKEN_URL")
    if client_id and client_secret and token_url:
        from utils import get_oauth_token

        token = get_oauth_token(token_url, client_id, client_secret)
        gateway_headers = {"Authorization": f"Bearer {token}"}
        print("Gateway auth: CUSTOM_JWT (OAuth token acquired)")
    else:
        print("Gateway auth: NONE (no CLIENT_ID/CLIENT_SECRET/TOKEN_URL in .env)")

    print(f"Gateway: {gateway_url}")

    mcp_client = MCPClient(
        lambda: streamablehttp_client(
            gateway_url,
            headers=gateway_headers,
            timeout=timedelta(seconds=120),
        )
    )

    with mcp_client:
        # 1) Tool surface check
        tool_names = [t.tool_name for t in mcp_client.list_tools_sync()]
        print(f"\nGateway exposes {len(tool_names)} tool(s): {tool_names}")
        expected = {f"{TARGET}___search_resources", f"{TARGET}___proxy_tool_call", f"{TARGET}___validate_endpoint"}
        missing = expected - set(tool_names)
        if missing:
            print(f"⚠️  Expected Bazaar tools not found: {sorted(missing)}")

        # 2) Default view vs. unfiltered view. The Bazaar returns curated results by
        # default, so we DON'T pass a redundant curatedOnly=true — we verify the default
        # is curated-only, then pass curatedOnly=false to lift the filter and let
        # uncurated resources through.
        print(f"\nProbing search_resources with {len(QUERIES)} queries (default, then curatedOnly=false)...")
        default_view, default_partial, default_skipped = discover(mcp_client, None)
        unfiltered, unfiltered_partial, unfiltered_skipped = discover(mcp_client, False)

    # Judge on each result's own curation flag (deterministic), not on set differences
    # between two truncated, relevance-ranked samples (which can differ by ranking alone).
    default_uncurated = sum(1 for _, (_, is_cur) in default_view.items() if not is_cur)
    unfiltered_uncurated = sum(1 for _, (_, is_cur) in unfiltered.items() if not is_cur)

    print(f"\n{'=' * 64}")
    print(f"Default view (no curatedOnly):       {len(default_view):3} distinct, {default_uncurated} uncurated")
    print(
        f"Unfiltered view (curatedOnly=false):{len(unfiltered):4} distinct, {unfiltered_uncurated} genuinely uncurated"
    )
    print(f"partialResults seen (more exist):    default={default_partial}, unfiltered={unfiltered_partial}")
    if default_skipped or unfiltered_skipped:
        print(f"probes skipped (schema validation):  default={default_skipped}, unfiltered={unfiltered_skipped}")
    print("(counts are query-dependent lower bounds — 20 results/call, no pagination)")
    print(f"{'=' * 64}")

    cats = categorize(default_view)
    if cats:
        print("\nCurated endpoints by category (sample):")
        for cat, n in sorted(cats.items(), key=lambda kv: -kv[1]):
            print(f"  {n:3}  {cat}")

    # Verdict — deterministic, from per-result curation metadata:
    #   PASS = the default search returns resources AND none are uncurated (Bazaar
    #          defaults to curated-only) AND curatedOnly=false surfaces genuinely-uncurated
    #          resources the default view hides (the filter meaningfully narrows results).
    print(f"\n{'=' * 64}")
    default_returns = len(default_view) > 0
    default_is_curated = default_returns and default_uncurated == 0
    filter_reveals_uncurated = unfiltered_uncurated > 0

    if default_is_curated and filter_reveals_uncurated:
        print("✅ PASS — curation is enabled and the curatedOnly filter is honored.")
        print(f"   the default search returns curated resources only ({len(default_view)} distinct, all")
        print(f"   tagged curated=true), and curatedOnly=false surfaced {unfiltered_uncurated} uncurated")
        print("   resource(s) the default view hides — so the filter genuinely narrows results.")
        code = 0
    elif default_returns and not default_is_curated:
        print("⚠️  UNEXPECTED — the default search returned uncurated resources, so this endpoint")
        print(f"   does not appear to default to curated-only ({default_uncurated} of {len(default_view)} untagged).")
        print("   Curation may be disabled for this endpoint.")
        code = 2
    elif default_is_curated and not filter_reveals_uncurated:
        print("⚠️  INCONCLUSIVE — the default view is curated-only, but no uncurated resources")
        print("   surfaced with curatedOnly=false across these probes. Curation may cover")
        print("   everything queried, or the probe set was too narrow. Broaden QUERIES.")
        code = 2
    else:
        print("❌ FAIL — the default search returned no endpoints. Check that the Bazaar target is")
        print("   reachable and that curation is enabled for this endpoint.")
        code = 1
    print(f"{'=' * 64}")
    sys.exit(code)


if __name__ == "__main__":
    main()
