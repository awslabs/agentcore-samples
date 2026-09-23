#!/usr/bin/env python3
"""Auth0 OAuth utilities for AWS AgentCore Registry: create, seed, and search.

This script provides end-to-end tooling for setting up and populating an
AWS AgentCore Registry that uses Auth0 CUSTOM_JWT authorization.

Key capabilities:
    - Authenticate via Auth0 client-credentials OAuth flow
    - Create a new registry with CUSTOM_JWT authorizer backed by Auth0
    - Seed the registry with sample agent records (weather, order-status,
      customer-support, inventory-lookup) and auto-approve them
    - Search registry records using OAuth bearer tokens

Configuration is loaded from a .env file (see .env.example) and requires:
    AWS_REGION, AWS_ACCOUNT_ID,
    AUTH0_DOMAIN, AUTH0_AUDIENCE.

Usage:
    # As a module
    from seed_records import create_registry, seed, search, get_token

    # As a script — seeds records into the registry specified by REGISTRY_ID
    python seed_records.py
"""

import boto3
import json
import logging
import os
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REGION = os.getenv("AWS_REGION", "us-west-2")
ACCOUNT_ID = os.getenv("AWS_ACCOUNT_ID")
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN")
AUDIENCE = os.getenv("AUTH0_AUDIENCE")


def _registry_arn(registry_id):
    return f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:registry/{registry_id}"


def _cp_client():
    return boto3.client("bedrock-agentcore-control", region_name=REGION)


def _dp_client():
    return boto3.client("bedrock-agentcore", region_name=REGION)


# ── Registry ──────────────────────────────────────────────────────────────────


def create_registry(
    name="auth0-oauth-registry",
    description="Registry with Auth0 OAuth authentication",
    poll_interval=5,
    max_wait=150,
):
    """Create an AgentCore registry with Auth0 CUSTOM_JWT authorizer.

    Returns dict with registryId, registryArn, and status.
    """
    cp = _cp_client()
    discovery_url = f"https://{AUTH0_DOMAIN}/.well-known/openid-configuration"

    logger.info("Creating registry '%s' with CUSTOM_JWT authorizer...", name)
    resp = cp.create_registry(
        name=name,
        description=description,
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": discovery_url,
                "allowedAudience": [AUDIENCE],
            }
        },
    )
    registry_arn = resp["registryArn"]
    registry_id = registry_arn.split("/")[-1]
    logger.info("Created registry %s (%s)", registry_id, registry_arn)

    status = "UNKNOWN"
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval
        info = cp.get_registry(registryId=registry_id)
        status = info.get("status", "UNKNOWN")
        logger.info("[%ds] status=%s", elapsed, status)
        if status == "READY":
            break
    else:
        logger.warning("Registry not READY after %ds — continuing anyway", max_wait)

    update_registry_audience_with_mcp_url(registry_id)
    logger.info("Added MCP URL to allowedAudience")

    result = {"registryId": registry_id, "registryArn": registry_arn, "status": status}
    logger.info("Done: %s", result)
    return result


def update_registry_audience_with_mcp_url(registry_id):
    """Add the MCP endpoint URL to the registry's allowedAudience."""
    cp = _cp_client()
    dp = _dp_client()
    registry = cp.get_registry(registryId=registry_id)
    jwt_config = registry["authorizerConfiguration"]["customJWTAuthorizer"]
    mcp_url = f"{dp.meta.endpoint_url}/registry/{registry_id}/mcp"
    audience = list(set(jwt_config.get("allowedAudience", []) + [mcp_url]))
    cp.update_registry(
        registryId=registry_id,
        authorizerConfiguration={
            "optionalValue": {
                "customJWTAuthorizer": {
                    "discoveryUrl": jwt_config["discoveryUrl"],
                    "allowedAudience": audience,
                }
            }
        },
    )
    while True:
        status = cp.get_registry(registryId=registry_id)["status"]
        if status != "UPDATING":
            break
        time.sleep(2)
    return cp.get_registry(registryId=registry_id)


# ── Seed ──────────────────────────────────────────────────────────────────────

RECORDS = [
    {
        "name": "weather_agent",
        "description": "Retrieves current weather conditions and 5-day forecasts for any city worldwide. Provides temperature, humidity, wind speed, and precipitation data.",
        "descriptorType": "CUSTOM",
        "descriptors": {
            "custom": {
                "inlineContent": json.dumps(
                    {
                        "type": "http-agent",
                        "team": "Platform",
                        "capabilities": [
                            "current weather",
                            "5-day forecast",
                            "severe weather alerts",
                        ],
                        "endpoint": "https://api.example.com/weather",
                    }
                )
            }
        },
    },
    {
        "name": "order_status_agent",
        "description": "Tracks order status, shipping updates, and estimated delivery times for e-commerce orders. Integrates with major carriers like UPS, FedEx, and USPS.",
        "descriptorType": "CUSTOM",
        "descriptors": {
            "custom": {
                "inlineContent": json.dumps(
                    {
                        "type": "http-agent",
                        "team": "Commerce",
                        "capabilities": [
                            "order tracking",
                            "shipping status",
                            "delivery estimates",
                            "return status",
                        ],
                        "endpoint": "https://api.example.com/orders",
                    }
                )
            }
        },
    },
    {
        "name": "customer_support_agent",
        "description": "Handles customer inquiries, processes refunds, and escalates issues. Uses knowledge base for FAQ resolution and sentiment analysis for prioritization.",
        "descriptorType": "CUSTOM",
        "descriptors": {
            "custom": {
                "inlineContent": json.dumps(
                    {
                        "type": "http-agent",
                        "team": "Support",
                        "capabilities": [
                            "FAQ resolution",
                            "refund processing",
                            "ticket escalation",
                            "sentiment analysis",
                        ],
                        "endpoint": "https://api.example.com/support",
                    }
                )
            }
        },
    },
    {
        "name": "inventory_lookup_agent",
        "description": "Checks real-time product inventory across warehouses and stores. Supports SKU lookup, stock level alerts, and reorder recommendations.",
        "descriptorType": "CUSTOM",
        "descriptors": {
            "custom": {
                "inlineContent": json.dumps(
                    {
                        "type": "http-agent",
                        "team": "Supply Chain",
                        "capabilities": [
                            "stock levels",
                            "warehouse lookup",
                            "reorder alerts",
                            "SKU search",
                        ],
                        "endpoint": "https://api.example.com/inventory",
                    }
                )
            }
        },
    },
]


def seed(registry_id):
    """Create records, submit for approval, and approve them.

    Returns list of created record dicts.
    """
    cp = _cp_client()
    created = []
    for rec in RECORDS:
        logger.info("Creating record '%s' (%s)...", rec["name"], rec["descriptorType"])
        try:
            resp = cp.create_registry_record(registryId=registry_id, **rec)
            record_id = resp["recordArn"].split("/")[-1]
            logger.info("  Created %s", record_id)
            created.append({"name": rec["name"], "recordId": record_id})
        except cp.exceptions.ConflictException:
            logger.info("  Already exists — skipping")
        except Exception as e:
            logger.error("  Failed: %s", e)

    if created:
        logger.info("Approving %d record(s)...", len(created))
        time.sleep(2)
        for rec in created:
            try:
                cp.submit_registry_record_for_approval(
                    registryId=registry_id,
                    recordId=rec["recordId"],
                )
                cp.update_registry_record_status(
                    registryId=registry_id,
                    recordId=rec["recordId"],
                    status="APPROVED",
                    statusReason="Auto-seed",
                )
                logger.info("  ✓ %s approved", rec["name"])
            except Exception as e:
                logger.error("  ✗ %s: %s", rec["name"], e)

    logger.info("Done — seeded %d record(s)", len(created))
    return created


def delete_registry(registry_id):
    """Delete all records in a registry, then delete the registry itself."""
    cp = _cp_client()
    records = cp.list_registry_records(registryId=registry_id).get("registryRecords", [])
    for rec in records:
        rid = rec["recordId"]
        logger.info("Deleting record %s...", rid)
        cp.delete_registry_record(registryId=registry_id, recordId=rid)
    logger.info("Deleting registry %s...", registry_id)
    resp = cp.delete_registry(registryId=registry_id)
    logger.info("Registry %s status: %s", registry_id, resp.get("status"))
    return resp


if __name__ == "__main__":
    seed()
