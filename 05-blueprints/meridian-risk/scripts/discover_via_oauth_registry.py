#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Discover records in a JWT-authorized registry over OAuth (no SigV4).

The AWS SDK/CLI always SigV4-signs, so it cannot search a CUSTOM_JWT registry.
This calls the discoverable data-plane API directly over HTTPS with an OAuth
bearer token:

    POST https://bedrock-agentcore.<region>.amazonaws.com/registry-records/search
    Authorization: Bearer <access token>
    {"searchQuery": "...", "registryIds": ["<jwt-registry-arn>"]}

Flow: assume the consumer role -> mint an M2M token via AgentCore Identity ->
POST the search over OAuth. A negative check (no token) must be rejected.

Exit 0 only if the OAuth search finds the record AND the no-token search is denied.
"""

import argparse
import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

import boto3

INFRA_DIR = pathlib.Path(__file__).resolve().parent.parent / "infra"
SEARCH_QUERY = "corporate KYC onboarding risk assessment"
ATTEMPTS = 6
INTERVAL_SECONDS = 10


def log(message: str) -> None:
    print(f"[oauth-discovery] {message}", flush=True)


def tf_output(name: str) -> dict:
    raw = subprocess.check_output(
        ["terraform", f"-chdir={INFRA_DIR}", "output", "-json", name]
    )
    data = json.loads(raw)
    if not data:
        raise SystemExit(f"{name} output is null — deploy with -var enable_registry_oauth_demo=true")
    return data


def assume(role_arn: str, region: str) -> boto3.Session:
    creds = boto3.client("sts", region_name=region).assume_role(
        RoleArn=role_arn, RoleSessionName="oauth-discovery-consumer"
    )["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region,
    )


def search(url: str, registry_arn: str, token: str | None) -> tuple[int, dict | str]:
    """POST the discovery search; optionally with a bearer token."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {"searchQuery": SEARCH_QUERY, "registryIds": [registry_arn], "maxResults": 10}
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()

    disc = tf_output("oauth_discovery_demo")
    wiring = tf_output("oauth_demo")
    region = disc["region"]

    session = assume(wiring["consumer_role_arn"], region)
    dp = session.client("bedrock-agentcore", region_name=region)

    log("minting M2M OAuth token via AgentCore Identity…")
    workload_token = dp.get_workload_access_token(
        workloadName=wiring["workload_name"]
    )["workloadAccessToken"]
    access_token = dp.get_resource_oauth2_token(
        workloadIdentityToken=workload_token,
        resourceCredentialProviderName=wiring["provider_name"],
        oauth2Flow="M2M",
        scopes=[wiring["scope"]],
    )["accessToken"]
    log("access token acquired")

    log(f"searching JWT registry over OAuth: POST {disc['search_url']}")
    found = False
    status = 0
    for attempt in range(1, ATTEMPTS + 1):
        status, payload = search(disc["search_url"], disc["registry_arn"], access_token)
        if status == 200 and isinstance(payload, dict):
            names = [r.get("name") for r in payload.get("registryRecords", [])]
            log(f"HTTP 200 — discovered records: {names or '(none yet)'}")
            if disc["record_name"] in names:
                found = True
                break
        else:
            log(f"HTTP {status}: {str(payload)[:300]}")
            if status not in (200, 404):
                break
        log(f"not indexed yet (attempt {attempt}/{ATTEMPTS}); waiting…")
        # nosemgrep: arbitrary-sleep — bounded wait for search indexing lag
        time.sleep(INTERVAL_SECONDS)

    negative_status, _ = search(disc["search_url"], disc["registry_arn"], token=None)
    log(f"search WITHOUT token -> HTTP {negative_status} (expect 401/403)")
    negative_ok = negative_status in (401, 403)

    log(
        f"RESULT: OAuth discovery {'PASS' if found else 'FAIL'}; "
        f"auth enforced {'PASS' if negative_ok else 'FAIL'}"
    )
    return 0 if (found and negative_ok) else 2


if __name__ == "__main__":
    sys.exit(main())
