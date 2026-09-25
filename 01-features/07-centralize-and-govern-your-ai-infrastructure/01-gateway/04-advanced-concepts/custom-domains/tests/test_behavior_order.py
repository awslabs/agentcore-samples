# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""CloudFront cache-behavior ordering.

CloudFront evaluates behaviors in list order and uses the FIRST match (it does
not prefer the most specific pattern). An A2A agent card sits *under* its
agent's live path (``/<agent>/.well-known/agent-card.json`` vs the live
``/<agent>/*``), so the discovery behavior must precede the live wildcard;
otherwise the card is served straight from the gateway with its ``url``
un-rewritten. This test synthesizes the template and locks that order in.
"""

import json
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import Environment
from aws_cdk.assertions import Template
from custom_domains.custom_domains_stack import CustomDomainsStack

_CONFIG = {
    "domain_name": "mcp.example.com",
    "geo_allowlist": ["US"],
    "routes": [
        {
            "path": "/",
            "gateway_url": "https://gw.gateway.bedrock-agentcore.us-east-1.amazonaws.com",
            "endpoints": [{"type": "http_a2a", "target_name": "planner"}],
        }
    ],
}


def _cache_behavior_patterns(tmp_path: Path):
    config_path = tmp_path / "agwcd.json"
    config_path.write_text(json.dumps(_CONFIG))
    app = cdk.App(context={"agwcd_config": str(config_path), "agwcd_stub_zone": True})
    stack = CustomDomainsStack(
        app,
        "CustomDomainsStack",
        env=Environment(account="123456789012", region="us-east-1"),
    )
    template = Template.from_stack(stack)
    dist = next(iter(template.find_resources("AWS::CloudFront::Distribution").values()))
    behaviors = dist["Properties"]["DistributionConfig"]["CacheBehaviors"]
    return [b["PathPattern"] for b in behaviors]


def test_agent_card_behavior_precedes_agent_wildcard(tmp_path):
    patterns = _cache_behavior_patterns(tmp_path)
    card = "/planner/.well-known/agent-card.json"
    wildcard = "/planner/*"
    assert card in patterns and wildcard in patterns
    # First-match semantics: the exact card path must be evaluated before the
    # live wildcard that also matches it.
    assert patterns.index(card) < patterns.index(wildcard)
