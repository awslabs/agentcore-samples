"""
Find AWS Marketplace sellers through an AgentCore Gateway `aws-marketplace` connector target.

The gateway exposes three Marketplace tools over MCP:
  SearchListings          free-text search, returns listing summaries (prodview-...)
  GetListing              listing details, including the productId
  ListFulfillmentOptions  the seller's API endpoint and how it's paid (x402)

The gateway uses the AWS_IAM authorizer, so requests are SigV4-signed with the
`bedrock-agentcore` signing name.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import urlparse

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


class GatewayToolError(Exception):
    pass


class GatewayMcpClient:
    """Minimal MCP client for an IAM-authorized AgentCore Gateway (JSON-RPC over HTTP).

    The gateway prefixes each tool name with its target name. setup_gateway.py names the
    target "marketplace"; pass `target_name` or set GATEWAY_TARGET_NAME if yours differs.
    """

    def __init__(
        self, gateway_url: str, region: str, session: boto3.Session | None = None, target_name: str | None = None
    ):
        self.url = gateway_url
        self.region = region
        self.session = session or boto3.Session(region_name=region)
        # Read at construction, not import, so a value in .env is picked up.
        self.tool_prefix = (target_name or os.environ.get("GATEWAY_TARGET_NAME") or "marketplace") + "___"
        self._id = 0
        self._http = requests.Session()
        self._initialized = False

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        req = AWSRequest(method="POST", url=self.url, data=body, headers=headers)
        SigV4Auth(self.session.get_credentials().get_frozen_credentials(), "bedrock-agentcore", self.region).add_auth(
            req
        )
        resp = self._http.post(self.url, data=body, headers=dict(req.headers), timeout=60)
        resp.raise_for_status()
        text = resp.text
        if resp.headers.get("content-type", "").startswith("text/event-stream"):
            # Use the last `data:` line of the stream, which carries the JSON-RPC result.
            text = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")][-1]
        msg = json.loads(text)
        if "error" in msg:
            raise GatewayToolError(msg["error"].get("message", str(msg["error"])))
        return msg["result"]

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            self._rpc(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "marketplace-buyer", "version": "1.0"},
                },
            )
            self._initialized = True

    def call_tool(self, name: str, arguments: dict) -> dict:
        self._ensure_initialized()
        result = self._rpc("tools/call", {"name": self.tool_prefix + name, "arguments": arguments})
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise GatewayToolError(text)
        return json.loads(text) if text else {}


@dataclass
class SellerEndpoint:
    product_id: str
    name: str
    url: str
    estimated_cost: str
    usage_instructions: str


class MarketplaceDiscovery:
    """Wraps the gateway tools and remembers which endpoints Marketplace returned, so the
    payer can be limited to sellers that were actually discovered through Marketplace."""

    def __init__(self, client: GatewayMcpClient):
        self.client = client
        self.discovered_hosts: set[str] = set()

    def search(self, text: str, max_results: int = 5) -> list[dict]:
        out = self.client.call_tool("SearchListings", {"searchText": text, "maxResults": max_results})
        return [
            {
                "listingId": s.get("listingId"),
                "name": s.get("listingName"),
                "description": s.get("shortDescription"),
                "publisher": (s.get("publisher") or {}).get("displayName"),
            }
            for s in out.get("listingSummaries", [])
        ]

    def get_listing(self, listing_id: str) -> dict:
        d = self.client.call_tool("GetListing", {"listingId": listing_id})
        products = [e["product"] for e in d.get("associatedEntities", []) if "product" in e]
        return {
            "listingId": d.get("listingId"),
            "name": d.get("listingName"),
            "description": d.get("longDescription") or d.get("shortDescription"),
            "productIds": [p["productId"] for p in products],
            "highlights": d.get("highlights", []),
            "pricing": [p.get("displayName") for p in d.get("pricingModels", [])],
        }

    def x402_endpoints(self, product_id: str) -> list[SellerEndpoint]:
        out = self.client.call_tool("ListFulfillmentOptions", {"productId": product_id})
        endpoints = []
        for fo in out.get("fulfillmentOptions", []):
            api = fo.get("apiFulfillmentOption") or {}
            protocols = [
                p
                for m in api.get("accessModels", [])
                for p in (m.get("agenticCommerce") or {}).get("paymentProtocols", [])
            ]
            if "X402" not in protocols:
                continue  # not payable with AWS Billing
            for ep in api.get("endpoints", []):
                url = ep.get("endpointUrl")
                if not url or not url.startswith("https://"):
                    continue
                self.discovered_hosts.add(urlparse(url).hostname)
                endpoints.append(
                    SellerEndpoint(
                        product_id,
                        ep.get("name", ""),
                        url,
                        ep.get("estimatedCostDescription", ""),
                        api.get("usageInstructions", ""),
                    )
                )
        return endpoints
