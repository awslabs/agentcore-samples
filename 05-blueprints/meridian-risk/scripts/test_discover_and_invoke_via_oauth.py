#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit tests for the pure card-parsing helper.

Run: .venv/bin/python -m unittest scripts.test_discover_and_invoke_via_oauth -v
(or, from the scripts/ directory: python -m unittest test_discover_and_invoke_via_oauth)

The repo has no wider test suite; scripts are otherwise validated live. This covers
the one pure function whose logic is worth pinning down without AWS.
"""

import importlib
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
consumer = importlib.import_module("discover_and_invoke_via_oauth")


def _descriptors(card: dict) -> dict:
    return {"a2a": {"agentCard": {"inlineContent": json.dumps(card)}}}


class ParseAgentEndpointTest(unittest.TestCase):
    def test_returns_url_and_scope_for_oauth_card(self):
        card = {
            "url": "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/x/invocations?qualifier=DEFAULT",
            "securitySchemes": {
                "oauth2": {
                    "type": "oauth2",
                    "flows": {
                        "clientCredentials": {
                            "tokenUrl": "https://d.auth.us-east-1.amazoncognito.com/oauth2/token",
                            "scopes": {"kyc-agent/invoke": "Invoke the KYC agent"},
                        }
                    },
                }
            },
        }
        url, scope = consumer.parse_agent_endpoint(_descriptors(card))
        self.assertEqual(url, card["url"])
        self.assertEqual(scope, "kyc-agent/invoke")

    def test_raises_when_no_oauth_scheme(self):
        card = {"url": "https://example.com/invocations", "securitySchemes": {}}
        with self.assertRaises(ValueError):
            consumer.parse_agent_endpoint(_descriptors(card))

    def test_raises_when_no_url(self):
        card = {
            "securitySchemes": {
                "oauth2": {"flows": {"clientCredentials": {"scopes": {"s": "d"}}}}
            }
        }
        with self.assertRaises(ValueError):
            consumer.parse_agent_endpoint(_descriptors(card))


if __name__ == "__main__":
    unittest.main()
