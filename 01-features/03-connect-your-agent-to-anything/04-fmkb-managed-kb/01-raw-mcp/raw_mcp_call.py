"""Call the FMKB Retrieve tool through the gateway via raw MCP+SigV4.

No agent in the loop. Useful as a smoke test before deploying anything to Runtime.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from mcp.client.session import ClientSession
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client


async def run(prompt: str, gateway_url: str, region: str, target_name: str) -> int:
    tool_name = f"{target_name}___Retrieve"
    async with aws_iam_streamablehttp_client(
        endpoint=gateway_url,
        aws_service="bedrock-agentcore",
        aws_region=region,
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            if tool_name not in names:
                print(f"tool {tool_name!r} not found. Available: {names}", file=sys.stderr)
                return 2
            result = await session.call_tool(tool_name, {"retrievalQuery": {"text": prompt}})
            for c in result.content:
                if hasattr(c, "text"):
                    try:
                        parsed = json.loads(c.text)
                    except json.JSONDecodeError:
                        print(c.text)
                        continue
                    for hit in parsed.get("retrievalResults", [])[:5]:
                        score = hit.get("score")
                        score_str = f"{score:.4f}" if isinstance(score, (int, float)) else "  n/a"
                        content = hit.get("content") or {}
                        # `content` is a tagged union — only TEXT chunks have `.text`.
                        if content.get("type") == "TEXT" or "text" in content:
                            text = (content.get("text") or "")[:300].replace("\n", " ")
                        else:
                            text = f"<{content.get('type', 'UNKNOWN')} chunk; not previewable>"
                        loc = hit.get("location") or {}
                        print(f"  score={score_str}  {text}…")
                        if loc:
                            print(f"    from: {json.dumps(loc)[:200]}")
            return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    args = parser.parse_args()

    missing = [k for k in ("GATEWAY_URL", "REGION", "TARGET_NAME") if not os.environ.get(k)]
    if missing:
        sys.exit(f"missing env vars {missing}; did you `source .env.fmkb-gateway`?")

    return asyncio.run(
        run(
            prompt=args.prompt,
            gateway_url=os.environ["GATEWAY_URL"],
            region=os.environ["REGION"],
            target_name=os.environ["TARGET_NAME"],
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
