#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Invoke the KYC harness with its S3 agent-skill attached.

Agent Skills are AgentSkills.io bundles (a SKILL.md plus optional scripts/refs)
that give the harness domain method on demand via progressive disclosure. The
service supports attaching a skill from S3, git, AWS's curated catalog, or the
microVM filesystem.

This stack ships the KYC onboarding skill in S3 (infra/harness.tf) and attaches
it PER INVOCATION rather than persisting it on the harness resource. The reason
is a provider gap, not a preference: the Terraform AWS provider's harness
resource models only the `path` skill source, and attaching an `s3` skill to the
harness makes the provider's next read fail outright ("reading Bedrock AgentCore
Harness: Unsupported Type — skill flatten: HarnessSkillMemberS3"), which breaks
plan/apply for the whole stack. Passing the skill on invoke_harness loads the
same bundle for that call without mutating the harness config Terraform reads.

Doubles as the skill smoke test: it prints the tools the loop called (look for
`skills`, the progressive-disclosure loader) and the verdict.

Requires a boto3 new enough to know invoke_harness and the s3 skill source
(the version in scripts/bootstrap.sh's venv); the system boto3 is usually older.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid

import boto3
from botocore.exceptions import ClientError


def _texts(event) -> list[str]:
    """Pull every `text` string out of a streamed event, at any depth."""
    found: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "text" and isinstance(value, str):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(event)
    return found


def invoke(region: str, harness_arn: str, s3_uri: str, prompt: str) -> int:
    client = boto3.client("bedrock-agentcore", region_name=region)
    session_id = uuid.uuid4().hex.ljust(33, "0")  # must be >= 33 chars

    print(f"[harness-skill] session {session_id}")
    print(f"[harness-skill] skill   {s3_uri}")
    try:
        response = client.invoke_harness(
            harnessArn=harness_arn,
            runtimeSessionId=session_id,
            skills=[{"s3": {"uri": s3_uri}}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
        )
    except ClientError as exc:
        print(f"[harness-skill] invoke_harness failed: {exc}", file=sys.stderr)
        return 1

    tool_events: list[str] = []
    text_events: list[dict] = []
    try:
        for event in response["stream"]:
            blob = json.dumps(event, default=str)
            if "toolUse" in blob:
                tool_events.append(blob)
            if '"text"' in blob:
                text_events.append(event)
    except Exception as exc:  # EventStreamError et al.
        print(f"[harness-skill] stream error: {str(exc)[:400]}", file=sys.stderr)
        return 1

    tools = sorted(set(re.findall(r'"name":\s*"([^"]+)"', " ".join(tool_events))))
    print(f"[harness-skill] tools called: {tools}")
    if "skills" in tools:
        print("[harness-skill]   ('skills' present → the skill bundle was loaded)")
    final = "".join(t for ev in text_events for t in _texts(ev))
    print("[harness-skill] verdict tail:\n" + (final[-600:] or "(no text captured)"))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument("--harness-arn", required=True)
    parser.add_argument("--s3-uri", required=True, help="s3://bucket/prefix/ of the skill")
    parser.add_argument(
        "--prompt",
        default=(
            "Assess corporate customer CUST003 for onboarding. Follow the "
            "kyc-onboarding-assessment skill. Return APPROVE/REJECT/ESCALATE with "
            "a 0-100 risk score and the key factors."
        ),
    )
    args = parser.parse_args()
    sys.exit(invoke(args.region, args.harness_arn, args.s3_uri, args.prompt))


if __name__ == "__main__":
    main()
