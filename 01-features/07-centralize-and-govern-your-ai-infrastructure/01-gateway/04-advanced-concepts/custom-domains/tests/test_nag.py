# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""Credential-free cdk-nag enforcement.

Synthesizes the stack with the Route 53 lookup stubbed (``agwcd_stub_zone``
context) so no AWS call is made, applies ``AwsSolutionsChecks``, and asserts
there are zero ``AwsSolutions-*`` errors. This turns the "0 Non-Compliant"
claim into a test runnable in CI without credentials.
"""

import json
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import Environment
from aws_cdk.assertions import Annotations, Match
from cdk_nag import AwsSolutionsChecks
from custom_domains.custom_domains_stack import CustomDomainsStack

_CONFIG = {
    "domain_name": "mcp.example.com",
    "geo_allowlist": ["US"],
    "routes": [
        {
            "path": "/sales",
            "gateway_url": "https://gw.gateway.bedrock-agentcore.us-east-1.amazonaws.com",
            "origin_verify": True,
            "endpoints": [
                {"type": "mcp"},
                {"type": "http_a2a", "target_name": "planner"},
            ],
        }
    ],
}


def _synth_stack(tmp_path: Path):
    config_path = tmp_path / "agwcd.json"
    config_path.write_text(json.dumps(_CONFIG))
    app = cdk.App(context={"agwcd_config": str(config_path), "agwcd_stub_zone": True})
    cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
    stack = CustomDomainsStack(
        app,
        "CustomDomainsStack",
        env=Environment(account="123456789012", region="us-east-1"),
    )
    app.synth()
    return stack


def test_cdk_nag_no_errors(tmp_path):
    stack = _synth_stack(tmp_path)
    errors = Annotations.from_stack(stack).find_error(
        "*", Match.string_like_regexp(r"AwsSolutions-.*")
    )
    messages = [e.entry.data for e in errors]
    assert errors == [], "cdk-nag AwsSolutions errors:\n" + "\n".join(messages)
