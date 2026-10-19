#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Discover the OAuth-invocable KYC agent in the Registry, then call it via OAuth.

This is the consumer side of AWS Agent Registry. The agent is found in the
registry; the caller then authenticates to it with an OAuth 2.0 bearer token —
no SigV4 on the agent call.

Flow:
  1. Read wiring from `terraform -chdir=infra output -json oauth_demo`.
  2. Assume the least-privilege consumer role.
  3. SearchRegistryRecords -> locate the record -> read endpoint + OAuth scope
     from its A2A agent card.
  4. GetWorkloadAccessToken -> GetResourceOauth2Token (M2M) -> access token.
  5. POST the agent endpoint with `Authorization: Bearer <token>` (positive).
  6. POST again with no token; it must be rejected (negative, proves enforcement).

Exit code 0 only if the positive call succeeds AND the negative call is rejected.
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
SAMPLE_PAYLOAD = {"customer_id": "CUST001", "assessment_type": "full"}
DISCOVERY_ATTEMPTS = 6
DISCOVERY_INTERVAL_SECONDS = 10


def log(message: str) -> None:
    print(f"[oauth-consumer] {message}", flush=True)


def parse_agent_endpoint(descriptors: dict) -> tuple[str, str]:
    """From an A2A record's descriptors, return (endpoint_url, oauth_scope).

    Raises ValueError if the card lacks a url or an OAuth2 client-credentials scope.
    This is the one pure unit worth testing directly (see the unittest sibling).
    """
    card = json.loads(descriptors["a2a"]["agentCard"]["inlineContent"])
    url = card.get("url")
    if not url:
        raise ValueError("agent card has no url")
    for scheme in card.get("securitySchemes", {}).values():
        client_credentials = (scheme.get("flows") or {}).get("clientCredentials")
        if client_credentials and client_credentials.get("scopes"):
            return url, next(iter(client_credentials["scopes"]))
    raise ValueError("agent card has no OAuth2 client-credentials scope")


def load_wiring() -> dict:
    """Read the `oauth_demo` Terraform output (the source of all wiring)."""
    raw = subprocess.check_output(
        ["terraform", f"-chdir={INFRA_DIR}", "output", "-json", "oauth_demo"]
    )
    data = json.loads(raw)
    if not data:
        raise SystemExit(
            "oauth_demo output is null — deploy with "
            "-var enable_registry_oauth_demo=true first."
        )
    return data


def assume(role_arn: str, region: str) -> boto3.Session:
    """Assume the least-privilege consumer role and return a scoped session."""
    creds = boto3.client("sts", region_name=region).assume_role(
        RoleArn=role_arn, RoleSessionName="oauth-demo-consumer"
    )["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region,
    )


def discover(dp, cp, registry_arn: str, registry_id: str, record_name: str) -> dict:
    """Search the registry for the record and return its descriptors.

    Retries because semantic search indexing lags ~30s behind approval.
    """
    for attempt in range(1, DISCOVERY_ATTEMPTS + 1):
        response = dp.search_registry_records(
            registryIds=[registry_arn], searchQuery=SEARCH_QUERY, maxResults=10
        )
        for record in response.get("registryRecords", []):
            if record["name"] == record_name:
                descriptors = record.get("descriptors")
                if not descriptors:  # search may omit descriptor content
                    descriptors = cp.get_registry_record(
                        registryId=registry_id, recordId=record["recordId"]
                    )["descriptors"]
                log(f"discovered '{record_name}' (recordId={record['recordId']})")
                return descriptors
        log(f"not indexed yet (attempt {attempt}/{DISCOVERY_ATTEMPTS}); waiting…")
        # nosemgrep: arbitrary-sleep — bounded wait for search indexing lag
        time.sleep(DISCOVERY_INTERVAL_SECONDS)
    raise SystemExit(f"record '{record_name}' not found via SearchRegistryRecords")


def http_post(url: str, payload: dict, token: str | None) -> tuple[int, str]:
    """POST JSON, optionally with a bearer token. Returns (status, body)."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:  # noqa: S310
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()

    wiring = load_wiring()
    region = wiring["region"]

    session = assume(wiring["consumer_role_arn"], region)
    dp = session.client("bedrock-agentcore", region_name=region)
    cp = session.client("bedrock-agentcore-control", region_name=region)

    log(f"discovering '{wiring['record_name']}' in the registry…")
    descriptors = discover(
        dp, cp, wiring["registry_arn"], wiring["registry_id"], wiring["record_name"]
    )
    endpoint, scope = parse_agent_endpoint(descriptors)
    log(f"endpoint = {endpoint}")
    log(f"required OAuth scope = {scope}")

    log("minting M2M OAuth token via AgentCore Identity…")
    workload_token = dp.get_workload_access_token(
        workloadName=wiring["workload_name"]
    )["workloadAccessToken"]
    access_token = dp.get_resource_oauth2_token(
        workloadIdentityToken=workload_token,
        resourceCredentialProviderName=wiring["provider_name"],
        oauth2Flow="M2M",
        scopes=[scope],
    )["accessToken"]
    log("client-credentials access token acquired")

    status, body = http_post(endpoint, SAMPLE_PAYLOAD, token=access_token)
    log(f"invoke WITH token  -> HTTP {status}")
    print(body[:2000])
    positive_ok = status == 200

    negative_status, _ = http_post(endpoint, SAMPLE_PAYLOAD, token=None)
    log(f"invoke WITHOUT token -> HTTP {negative_status} (expect 401/403)")
    negative_ok = negative_status in (401, 403)

    log(
        f"RESULT: OAuth invoke {'PASS' if positive_ok else 'FAIL'}; "
        f"auth enforced {'PASS' if negative_ok else 'FAIL'}"
    )
    return 0 if (positive_ok and negative_ok) else 2


if __name__ == "__main__":
    sys.exit(main())
