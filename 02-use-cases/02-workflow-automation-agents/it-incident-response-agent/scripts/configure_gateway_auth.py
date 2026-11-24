#!/usr/bin/env python3
"""Local-only CUSTOM_JWT configuration; no credentials or service calls."""

import json
import os
from pathlib import Path


def configure(project: Path, discovery_url: str, audience: str, client_id: str, scopes: list[str]) -> None:
    file = project / "agentcore/agentcore.json"
    spec = json.loads(file.read_text())
    if spec.get("version") != 2:
        raise ValueError("This helper requires the migrated version 2 project")
    gateway = next(g for g in spec["agentCoreGateways"] if g["name"] == "ITIncidentGateway")
    gateway["authorizerType"] = "CUSTOM_JWT"
    gateway["authorizerConfiguration"] = {
        "customJwtAuthorizer": {
            "discoveryUrl": discovery_url,
            "allowedAudience": [audience],
            "allowedClients": [client_id],
            **({"allowedScopes": scopes} if scopes else {}),
        }
    }
    gateway.pop("customJwtAuthorizer", None)  # legacy v0 location
    # Keep semantic search, targets, policy attachment and identity names intact.
    runtime = next(r for r in spec["runtimes"] if r["name"] == "ITIncidentAgent")
    if not scopes or any(not s or any(c.isspace() for c in s) for s in scopes):
        raise ValueError("Specify explicit M2M scopes allowed by the Gateway and IdP")
    updates = {
        "GATEWAY_AUTH_MODE": "CUSTOM_JWT",
        "GATEWAY_OAUTH_PROVIDER_NAME": spec["name"] + "-gateway-m2m",
        "GATEWAY_OAUTH_AUDIENCE": audience,
        "GATEWAY_OAUTH_SCOPES": " ".join(sorted(set(scopes))),
    }
    runtime["envVars"] = [v for v in runtime.get("envVars", []) if v["name"] not in updates]
    runtime["envVars"].extend({"name": name, "value": value} for name, value in updates.items())
    file.write_text(json.dumps(spec, indent=2) + "\n")


if __name__ == "__main__":
    configure(
        Path(__file__).resolve().parents[1],
        os.environ["DISCOVERY_URL"],
        os.environ["AUTH0_AUDIENCE"],
        os.environ["AUTH0_CLIENT_ID"],
        os.environ.get("AUTH0_SCOPES", "").split(),
    )
