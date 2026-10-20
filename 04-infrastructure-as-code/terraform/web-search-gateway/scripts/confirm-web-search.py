#!/usr/bin/env python3
"""
confirm-web-search.py
-----------------------------------------------------------------------------
Demo proof for the Terraform AgentCore Web Search PoC.

Proves that the Gateway provisioned by Terraform exposes the AWS-managed Web
Search connector and returns live web results -- WITHOUT deploying an agent.
It speaks MCP to the Gateway directly:

  1. SigV4-signs a POST to the Gateway's /mcp endpoint (inbound auth = AWS_IAM).
  2. Calls `tools/list` and asserts `<target>___WebSearch` is present.
  3. Calls `tools/call` with a natural-language query.
  4. Prints a PASS / FAIL verdict plus each result's title, URL and date.

Dated results with real source URLs are the proof this is live web data rather
than model training knowledge -- there is no model in this path at all.

Requires only botocore (bundled with boto3/awscli) and the standard library.

Usage:
    python3 scripts/confirm-web-search.py
    python3 scripts/confirm-web-search.py --query "Who won the F1 race this weekend?"
    python3 scripts/confirm-web-search.py --include sec.gov --include reuters.com
    python3 scripts/confirm-web-search.py --from 2026-10-01 --max-results 10
    python3 scripts/confirm-web-search.py --no-assume          # use ambient creds
    python3 scripts/confirm-web-search.py --assume-role-arn arn:aws:iam::...:role/other
    python3 scripts/confirm-web-search.py --raw                # dump wire JSON
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

try:
    import boto3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
except ImportError:  # pragma: no cover
    sys.exit("ERROR: botocore/boto3 not importable.\n       Install with: python3 -m pip install boto3")

SERVICE = "bedrock-agentcore"
MAX_QUERY_CHARS = 200
DEFAULT_QUERY = "What are the top AI news headlines today, with sources and dates?"

# ---------------------------------------------------------------------------
# Terminal output helpers
# ---------------------------------------------------------------------------

_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def step(msg: str) -> None:
    print(_c("1;36", msg))


def ok(msg: str) -> None:
    print(_c("1;32", msg))


def warn(msg: str) -> None:
    print(_c("1;33", msg))


def err(msg: str) -> None:
    # Flush stdout first so the narrative and the error stay in order even when
    # both streams are piped into the same terminal.
    sys.stdout.flush()
    print(_c("1;31", msg), file=sys.stderr)
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# Terraform output discovery
# ---------------------------------------------------------------------------


def terraform_outputs() -> dict[str, Any]:
    """Read `terraform output -json` from the repo root (parent of scripts/)."""
    root = pathlib.Path(__file__).resolve().parent.parent
    try:
        proc = subprocess.run(
            ["terraform", "output", "-json"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError:
        return {}
    except subprocess.TimeoutExpired:
        return {}
    if proc.returncode != 0:
        return {}
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    return {k: v.get("value") for k, v in raw.items()}


def ssl_context(ca_bundle: str | None = None) -> ssl.SSLContext:
    """Build a verifying SSL context with a CA bundle we can actually find.

    Some Python builds (notably the python.org macOS installers) ship with no
    system CA file, so urllib fails with CERTIFICATE_VERIFY_FAILED even though
    boto3 works fine -- boto3 uses botocore's bundled cacert.pem. Resolve a
    bundle explicitly rather than relying on the OpenSSL defaults.
    """
    candidates = [
        ca_bundle,
        os.environ.get("AWS_CA_BUNDLE"),
        os.environ.get("REQUESTS_CA_BUNDLE"),
        os.environ.get("SSL_CERT_FILE"),
    ]
    try:
        import certifi

        candidates.append(certifi.where())
    except ImportError:
        pass
    try:
        from botocore.httpsession import DEFAULT_CA_BUNDLE

        candidates.append(DEFAULT_CA_BUNDLE)
    except ImportError:
        pass

    for path in candidates:
        if path and os.path.exists(path):
            return ssl.create_default_context(cafile=path)

    # Nothing found; fall back to the platform defaults (still verifying).
    return ssl.create_default_context()


def region_from_url(url: str) -> str | None:
    """Infer the Region from a Gateway hostname.

    Gateway URLs look like:
        <gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com
    """
    host = urlparse(url).hostname or ""
    parts = host.split(".")
    if "bedrock-agentcore" in parts:
        idx = parts.index("bedrock-agentcore")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None


def normalize_mcp_url(url: str) -> str:
    """Ensure the URL points at the MCP endpoint.

    The control plane's `gatewayUrl` usually already ends in /mcp but the
    related `endpoint` field does not. Guard rather than assume so a stray
    value does not turn into a 404 mid-demo.
    """
    url = url.rstrip("/")
    if urlparse(url).scheme != "https":
        raise SystemExit(f"ERROR: refusing to send SigV4-signed credentials over a non-HTTPS URL: {url}")
    return url if url.endswith("/mcp") else url + "/mcp"


# ---------------------------------------------------------------------------
# Signed MCP transport
# ---------------------------------------------------------------------------


class McpClient:
    """Minimal MCP-over-HTTP client that SigV4-signs every request."""

    def __init__(
        self,
        url: str,
        region: str,
        session: boto3.Session,
        protocol_version: str,
        ca_bundle: str | None = None,
    ):
        if urlparse(url).scheme != "https":
            raise SystemExit(f"ERROR: Gateway URL must be https, got: {url}")
        self.url = url
        self.region = region
        self.protocol_version = protocol_version
        self._ssl = ssl_context(ca_bundle)
        creds = session.get_credentials()
        if creds is None:
            raise SystemExit("ERROR: no AWS credentials found. Configure a profile or run `aws login`.")
        self._creds = creds.get_frozen_credentials()
        self._signer = SigV4Auth(self._creds, SERVICE, region)
        self._id = 0

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.protocol_version,
        }

        # Sign the request. botocore adds Authorization, X-Amz-Date and
        # X-Amz-Security-Token (for session credentials), and folds in the Host
        # header derived from the URL.
        aws_req = AWSRequest(method="POST", url=self.url, data=body, headers=headers)
        self._signer.add_auth(aws_req)

        req = urllib.request.Request(
            self.url,
            data=body.encode(),
            headers=dict(aws_req.headers),
            method="POST",
        )

        try:
            # nosec B310 - the https scheme is enforced in __init__ and
            # normalize_mcp_url; TLS verification is on via self._ssl.
            with urllib.request.urlopen(req, timeout=120, context=self._ssl) as resp:
                payload = resp.read().decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace").strip()
            raise McpError(f"HTTP {exc.code} {exc.reason}", detail) from exc
        except urllib.error.URLError as exc:
            hint = ""
            if "CERTIFICATE_VERIFY_FAILED" in str(exc.reason):
                hint = (
                    "\n    TLS trust store problem, not an AWS problem. Pass "
                    "--ca-bundle /path/to/cacert.pem,\n    or set AWS_CA_BUNDLE."
                )
            raise McpError("connection failed", f"{exc.reason}{hint}") from exc

        envelope = _parse_body(payload)

        if "error" in envelope:
            e = envelope["error"]
            raise McpError(
                f"JSON-RPC error {e.get('code', '?')}: {e.get('message', 'unknown')}",
                json.dumps(e.get("data", {}), indent=2) if e.get("data") else "",
            )

        return envelope.get("result", {})


class McpError(RuntimeError):
    def __init__(self, summary: str, detail: str = ""):
        super().__init__(summary)
        self.summary = summary
        self.detail = detail


def _parse_body(payload: str) -> dict[str, Any]:
    """Parse a plain JSON body or an SSE-framed one."""
    payload = payload.strip()
    if not payload:
        raise McpError("empty response body")

    if payload.startswith("{"):
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise McpError("response was not valid JSON", payload[:500]) from exc

    # Server-sent events: pull the last `data:` frame.
    frames = [line[len("data:") :].strip() for line in payload.splitlines() if line.startswith("data:")]
    if not frames:
        raise McpError("response was neither JSON nor SSE", payload[:500])
    try:
        return json.loads(frames[-1])
    except json.JSONDecodeError as exc:
        raise McpError("SSE frame was not valid JSON", frames[-1][:500]) from exc


# ---------------------------------------------------------------------------
# Result rendering
# ---------------------------------------------------------------------------


def extract_results(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the result list out of an MCP tools/call response.

    The Web Search connector returns content[0].text as a JSON-ENCODED STRING
    containing {"id": ..., "results": [...]}, so it needs a second decode.
    """
    content = result.get("content") or []
    for block in content:
        if block.get("type") != "text":
            continue
        text = block.get("text", "")
        try:
            inner = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(inner, dict) and isinstance(inner.get("results"), list):
            return inner["results"]
    return []


def tool_error_text(result: dict[str, Any]) -> str:
    """Pull the human-readable message out of an isError=true tools/call result."""
    parts = [
        block.get("text", "").strip()
        for block in (result.get("content") or [])
        if block.get("type") == "text" and block.get("text")
    ]
    return " ".join(p for p in parts if p) or json.dumps(result)[:500]


def render(results: list[dict[str, Any]]) -> None:
    for i, r in enumerate(results, 1):
        title = r.get("title") or "(untitled)"
        print(f"  {_c('1', f'{i}. {title}')}")
        if r.get("url"):
            print(f"     {_c('4', r['url'])}")
        if r.get("publishedDate"):
            print(f"     published: {r['publishedDate']}")
        snippet = (r.get("text") or "").strip().replace("\n", " ")
        if snippet:
            print(f"     {snippet[:220]}{'...' if len(snippet) > 220 else ''}")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_parser(tf: dict[str, Any]) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Prove the AgentCore Gateway Web Search tool works, over MCP with SigV4.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--query", "-q", default=DEFAULT_QUERY, help="Search query (max 200 chars).")
    p.add_argument("--url", default=tf.get("mcp_endpoint"), help="Gateway MCP endpoint.")
    p.add_argument("--region", default=None, help="AWS Region of the Gateway.")
    p.add_argument("--tool-name", default=tf.get("tool_name"), help="Expected MCP tool name.")
    p.add_argument("--protocol-version", default="2025-11-25", help="MCP-Protocol-Version header value.")
    p.add_argument("--max-results", type=int, default=5, help="Number of results to request (1-25).")
    p.add_argument(
        "--assume-role-arn",
        default=tf.get("demo_caller_role_arn"),
        help="Role to assume before calling (defaults to the Terraform demo caller role).",
    )
    p.add_argument(
        "--no-assume",
        action="store_true",
        help="Skip AssumeRole and use ambient credentials.",
    )
    p.add_argument("--profile", default=None, help="AWS profile to use.")
    p.add_argument(
        "--ca-bundle",
        default=None,
        help="CA bundle for TLS verification (defaults to certifi/botocore's bundle).",
    )
    p.add_argument("--include", action="append", default=[], help="Request-level domain allowlist (repeatable).")
    p.add_argument("--exclude", action="append", default=[], help="Request-level domain denylist (repeatable).")
    p.add_argument("--from", dest="date_from", default=None, help="Earliest publish date (ISO-8601).")
    p.add_argument("--to", dest="date_to", default=None, help="Latest publish date (ISO-8601).")
    p.add_argument("--raw", action="store_true", help="Dump raw JSON-RPC results.")
    return p


def main() -> int:
    tf = terraform_outputs()
    args = build_parser(tf).parse_args()

    if not args.url:
        err("ERROR: could not determine the Gateway MCP endpoint.")
        err("       Run `terraform apply` first, or pass --url explicitly.")
        return 2

    url = normalize_mcp_url(args.url)
    tool_name = args.tool_name or "web-search___WebSearch"

    if len(args.query) > MAX_QUERY_CHARS:
        err(f"ERROR: query is {len(args.query)} chars; the Web Search tool accepts at most {MAX_QUERY_CHARS}.")
        return 2
    if not 1 <= args.max_results <= 25:
        err("ERROR: --max-results must be between 1 and 25.")
        return 2

    # Region: explicit flag, else infer from the Gateway hostname, else session.
    region = args.region or region_from_url(url)

    base = boto3.Session(profile_name=args.profile, region_name=region)
    region = region or base.region_name
    if not region:
        err("ERROR: could not determine the Region. Pass --region.")
        return 2

    # ---- Credentials ------------------------------------------------------
    session = base
    if args.assume_role_arn and not args.no_assume:
        is_demo_role = args.assume_role_arn == tf.get("demo_caller_role_arn")
        step("==> 0/3  Assuming caller role")
        print(f"    role   : {args.assume_role_arn}")
        try:
            creds = base.client("sts", region_name=region).assume_role(
                RoleArn=args.assume_role_arn,
                RoleSessionName="websearch-poc-demo",
            )["Credentials"]
        except Exception as exc:  # noqa: BLE001 - surface any STS failure verbatim
            err(f"    AssumeRole failed: {exc}")
            err("    Re-run with --no-assume to use your ambient credentials instead.")
            return 1
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=region,
        )
        if is_demo_role:
            ok("    assumed (this role may invoke the Gateway and nothing else)")
        else:
            ok("    assumed")
    else:
        step("==> 0/3  Using ambient credentials")
        try:
            who = base.client("sts", region_name=region).get_caller_identity()
            print(f"    identity: {who['Arn']}")
        except Exception as exc:  # noqa: BLE001
            err(f"    could not resolve caller identity: {exc}")
            return 1
    print()

    client = McpClient(url, region, session, args.protocol_version, args.ca_bundle)

    # ---- Step 1: discover -------------------------------------------------
    step("==> 1/3  Listing tools on the Gateway (tools/list)")
    print(f"    endpoint: {url}")
    print(f"    region  : {region}")
    try:
        listing = client.call("tools/list")
    except McpError as exc:
        err(f"    {exc.summary}")
        if exc.detail:
            err(f"    {exc.detail}")
        print()
        err("FAIL  Could not list tools on the Gateway.")
        if "403" in exc.summary or "AccessDenied" in exc.detail:
            err("      The calling identity lacks bedrock-agentcore:InvokeGateway on the")
            err("      Gateway ARN. This is inbound AWS_IAM auth working as intended.")
        return 1

    tools = listing.get("tools", [])
    names = [t.get("name") for t in tools]
    ok(f"    {len(tools)} tool(s) discovered")
    for n in names:
        marker = "<--" if n == tool_name else "   "
        print(f"      - {n} {marker}")

    match = next((t for t in tools if t.get("name") == tool_name), None)
    if match is None:
        print()
        err(f"FAIL  Expected tool '{tool_name}' was not found on the Gateway.")
        err("      Check that the Web Search target provisioned: terraform output target_id")
        return 1

    schema_props = list((match.get("inputSchema") or {}).get("properties", {}).keys())
    print(f"    input schema: {', '.join(schema_props) or '(none reported)'}")
    if "filters" in schema_props:
        print("    'filters' present -> connector 1.2.0 pinning is in effect")
    else:
        warn("    'filters' absent -> connector is pre-1.2.0; request-level filters unavailable")
    print()

    # ---- Step 2: invoke ---------------------------------------------------
    step("==> 2/3  Calling the Web Search tool (tools/call)")
    arguments: dict[str, Any] = {"query": args.query, "maxResults": args.max_results}

    # NOTE: at request level the domain filter nests under `filters`. At target
    # level (Terraform parameter_values) `domainFilter` sits at the top level.
    domain_filter = {}
    if args.include:
        domain_filter["include"] = args.include
    if args.exclude:
        domain_filter["exclude"] = args.exclude
    date_filter = {}
    if args.date_from:
        date_filter["from"] = args.date_from
    if args.date_to:
        date_filter["to"] = args.date_to
    filters = {}
    if domain_filter:
        filters["domainFilter"] = domain_filter
    if date_filter:
        filters["publishedDateFilter"] = date_filter
    if filters:
        arguments["filters"] = filters

    print(f"    query   : {args.query}")
    if filters:
        print(f"    filters : {json.dumps(filters)}")
        print("              (composes with the target-level lists; can narrow, never widen)")

    try:
        result = client.call("tools/call", {"name": tool_name, "arguments": arguments})
    except McpError as exc:
        err(f"    {exc.summary}")
        if exc.detail:
            err(f"    {exc.detail}")
        print()
        err("FAIL  The tool call did not succeed.")
        return 1

    if result.get("isError"):
        detail = tool_error_text(result)
        print()
        step("==> 3/3  Verdict")
        line = "-" * 63
        print(line)

        # A request-level include list that doesn't intersect the admin-level
        # allowlist is refused outright. That is the governance model working:
        # per-request filters can narrow, never widen.
        if "no overlap" in detail or "admin domainFilter" in detail:
            warn("BLOCKED  The request-level filter was refused by the target-level policy.")
            print(f"         {detail}")
            print()
            print("         This is the governance model working as intended: a per-request")
            print("         filter composes with the admin list and can only narrow it.")
            print("         No agent or prompt injection can widen the allowed domains.")
            print(line)
            return 3

        err("FAIL  The Web Search tool returned isError=true.")
        if detail:
            err(f"      {detail}")
        print(line)
        return 1

    results = extract_results(result)
    ok(f"    tool responded with {len(results)} result(s)")
    print()

    # ---- Step 3: verdict --------------------------------------------------
    step("==> 3/3  Verdict")
    line = "-" * 63
    print(line)
    if results:
        ok("PASS  The Gateway invoked the AWS-managed Web Search connector.")
        print(f"      tool: {tool_name}")
        print("      Queries were served inside AWS -- no third-party search API,")
        print("      no API keys, no model in this path.")
    else:
        err("FAIL  The call succeeded but returned zero results.")
        if args.include:
            err("      A request-level include list can narrow results to nothing.")
            err("      Try again without --include.")
    print(line)
    print()

    if results:
        step("Live results:")
        render(results)

    if args.raw:
        step("Raw tools/call result:")
        print(json.dumps(result, indent=2))

    return 0 if results else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
