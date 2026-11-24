# /// script
# requires-python = ">=3.12"
# dependencies = ["boto3==1.44.9", "botocore==1.44.9", "jmespath==1.1.0", "s3transfer==0.19.3", "python-dateutil==2.9.0.post0", "six==1.17.0", "urllib3==2.8.0"]
# ///
"""Offline policy-model preflight and read-only GetPolicy using the pinned SDK.

Never installs/injects models or changes user configuration. Run preflight before
provisioning. SDK model shadowing is independent of the installed version label.
"""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path

import boto3
import botocore
import botocore.session
from botocore.model import ServiceModel

VERSIONS = {
    "boto3": "1.44.9",
    "botocore": "1.44.9",
    "jmespath": "1.1.0",
    "s3transfer": "0.19.3",
    "python-dateutil": "2.9.0.post0",
    "six": "1.17.0",
    "urllib3": "2.8.0",
}
# Canonical service-2 model from the SHA-verified published botocore wheel.
MODEL_SHA256 = "65a81c44be9752576a0f03cc1292d0f992e638987e8064894d138c3e1a68a4db"
SERVICE = "bedrock-agentcore-control"
API_VERSION = "2023-06-05"


def require_policy_model(session=None):
    """Check model metadata without clients/service calls; credentials are not required."""
    for package, expected in VERSIONS.items():
        if importlib.metadata.version(package) != expected:
            raise RuntimeError("Unsupported control SDK graph; install requirements-control.txt with hashes")
    session = session or botocore.session.Session()
    loader = session.get_component("data_loader")
    # Match Session.create_client(api_version=None), not get_service_model's
    # different config handling. Configured versions precede latest-directory
    # resolution. Reject a divergent selection before pinning any client.
    selected = session.get_config_variable("api_versions").get(SERVICE)
    if selected is None:
        selected = loader.determine_latest_version(SERVICE, "service-2")
    if selected != API_VERSION:
        raise RuntimeError(
            f"Unsupported/shadowed policy API selection {selected!r}; expected {API_VERSION}. "
            "Inspect api_versions and model search paths; no clients or intents may be created."
        )
    _, origin = loader.load_data_with_path(f"{SERVICE}/{selected}/service-2")
    bundled = Path(botocore.__file__).resolve().parent / "data" / SERVICE / API_VERSION / "service-2"
    data = loader.load_service_model(SERVICE, "service-2", selected)
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if Path(origin).resolve() != bundled or digest != MODEL_SHA256:
        raise RuntimeError(
            f"Unsupported/shadowed policy model at {origin} (sha256 {digest}). "
            "No provisioning is safe with this model. See README control-environment preflight; "
            "use a private HOME and no AWS_DATA_PATH override while preserving explicit authorized "
            "AWS config/credential paths. Do not delete global models or edit deployment ledgers."
        )
    return session.get_service_model(SERVICE, selected)


def verified_policy_session():
    """The same checked session must be passed to boto3; do not reconstruct it."""
    sdk = botocore.session.Session()
    require_policy_model(sdk)
    return sdk


def require_policy_client_model(client):
    """Also refuse a separately supplied SDK client with a divergent model.

    Model-only offline doubles may lack SDK metadata; the default-selection
    guard still runs before direct helper calls. Real botocore clients expose
    a ServiceModel on meta.service_model.
    """
    model = getattr(getattr(client, "meta", None), "service_model", None)
    if isinstance(model, ServiceModel):
        digest = hashlib.sha256(
            json.dumps(model._service_description, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if model.api_version != API_VERSION or digest != MODEL_SHA256:
            raise RuntimeError("Unsupported policy client model; retain intents and use the verified session/API")


def policy_statement(definition):
    """Both published plain-statement variants, never ambiguous unions/generation."""
    if not isinstance(definition, dict) or set(definition) not in ({"cedar"}, {"policy"}):
        raise RuntimeError("Unexpected policy definition; retain ledger and inspect")
    value = next(iter(definition.values()))
    if not isinstance(value, dict) or set(value) != {"statement"} or not isinstance(value["statement"], str):
        raise RuntimeError("Unexpected policy statement; retain ledger and inspect")
    return value["statement"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    get = sub.add_parser("get-policy", help="Read full parsed policy, including enforcementMode (no mutation)")
    get.add_argument("--policy-engine-id", required=True)
    get.add_argument("--policy-id", required=True)
    get.add_argument("--region", required=True)
    args = parser.parse_args()
    sdk = verified_policy_session()
    if args.command is None:
        print(json.dumps({"sdk": VERSIONS, "model_sha256": MODEL_SHA256, "preflight": "PASS", "aws_calls": 0}))
        return
    ctrl = boto3.Session(botocore_session=sdk, region_name=args.region).client(SERVICE, api_version=API_VERSION)
    response = ctrl.get_policy(policyEngineId=args.policy_engine_id, policyId=args.policy_id)
    print(json.dumps(response, default=lambda value: value.isoformat(), indent=2))


if __name__ == "__main__":
    main()
