"""Local ledger and read-only ownership checks for this dedicated fresh lab.

Not an importer for deployed v0 stacks. Never repair an uncertain ledger by
inventing IDs or ownership tags. Run only one setup/cleanup process at a time.
"""

import json
import os
import re
import tempfile
from pathlib import Path

from botocore.exceptions import ClientError

OWNER_TAG = "banking-sample-owner"
NOT_FOUND = {"ResourceNotFoundException", "NotFound", "NoSuchEntity"}


def absent(error):
    return error.response["Error"]["Code"] in NOT_FOUND


def atomic_save(path: Path, data: dict):
    """An API must not run until this durable atomic checkpoint succeeds."""
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def require_equal(actual, expected, label):
    if actual != expected:
        raise RuntimeError(f"{label} mismatch/drift; retain setup_config.json and audit ownership")


def validate_ledger(cfg, *, allow_unresolved_intents=False):
    for key in ("account_id", "region", "role_name", "gateway_name", "engine_name"):
        if not isinstance(cfg.get(key), str) or not cfg[key]:
            raise RuntimeError(f"Missing {key}; legacy state needs a separate reviewed migration")
    if not re.fullmatch(r"\d{12}", cfg["account_id"]):
        raise RuntimeError("Invalid account_id")
    for kind, service, resource in (
        ("role", "iam", "role"),
        ("engine", "bedrock-agentcore", "policy-engine"),
        ("gateway", "bedrock-agentcore", "gateway"),
    ):
        arn = cfg.get(kind + "_arn")
        identity = cfg.get("role_name" if kind == "role" else kind + "_id")
        if arn:
            # IAM roles created here have the default / path, not arbitrary paths.
            expected = (
                f"arn:aws:{service}:{'' if kind == 'role' else cfg['region']}:{cfg['account_id']}:{resource}/{identity}"
            )
            require_equal(arn, expected, kind + " ARN/account/region/identity")
        elif kind != "role" and identity:
            intent = cfg.get("create_intents", {}).get(kind, {})
            require_equal(intent.get("params", {}).get("name"), cfg[kind + "_name"], kind + " partial-create intent")
    if not cfg.get("ownership_id"):
        raise RuntimeError("Missing ownership_id; do not adopt legacy resources by editing the ledger")
    if not allow_unresolved_intents:
        for key, intent in cfg.get("create_intents", {}).items():
            if key == "target":
                resolved = cfg.get("target_id_" + intent["params"]["name"])
            elif key.startswith("policy:"):
                resolved = cfg.get("base_policy_ids", {}).get(key.removeprefix("policy:"))
            elif key in ("role", "engine", "gateway"):
                resolved = cfg.get({"role": "role_arn", "engine": "engine_id", "gateway": "gateway_id"}[key])
            else:
                raise RuntimeError(f"Unknown create intent {key}; retain ledger and audit")
            if not resolved:
                raise RuntimeError(f"Unresolved {key} create intent; retry setup with the same ledger before cleanup")


def read_present(get_resource):
    try:
        return get_resource()
    except ClientError as error:
        if absent(error):
            return None
        raise


def verify_resources(ctrl, iam, cfg, *, allow_unresolved_intents=False):
    """All read-only preflight, before *any* destructive operation.

    Returned IDs with a pending/failed create are legitimate; readiness is not
    required for cleanup, but identity and the unique ownership tag still are.
    """
    validate_ledger(cfg, allow_unresolved_intents=allow_unresolved_intents)
    owner = cfg["ownership_id"]
    for kind, id_key, arn_key, getter in (
        (
            "engine",
            "policyEngineId",
            "policyEngineArn",
            lambda: ctrl.get_policy_engine(policyEngineId=cfg["engine_id"]),
        ),
        ("gateway", "gatewayId", "gatewayArn", lambda: ctrl.get_gateway(gatewayIdentifier=cfg["gateway_id"])),
    ):
        if not cfg.get(kind + "_id"):
            continue
        current = read_present(getter)
        if current is None:
            continue
        expected_arn = f"arn:aws:bedrock-agentcore:{cfg['region']}:{cfg['account_id']}:{'policy-engine' if kind == 'engine' else 'gateway'}/{cfg[kind + '_id']}"
        require_equal(current.get(id_key), cfg[kind + "_id"], kind + " ID")
        require_equal(current.get(arn_key), expected_arn, kind + " ARN")
        require_equal(current.get("name"), cfg[kind + "_name"], kind + " name")
        tags = ctrl.list_tags_for_resource(resourceArn=expected_arn)["tags"]
        require_equal(tags.get(OWNER_TAG), owner, kind + " ownership tag")
        if kind == "gateway":
            require_equal(current.get("roleArn"), cfg.get("role_arn"), "gateway role reference")
            require_equal(
                current.get("policyEngineConfiguration", {}).get("arn"),
                cfg.get("engine_arn"),
                "gateway engine reference",
            )
            if cfg.get("workload_identity_arn"):
                require_equal(
                    current.get("workloadIdentityDetails", {}).get("workloadIdentityArn"),
                    cfg["workload_identity_arn"],
                    "gateway workload identity",
                )
    if cfg.get("role_arn"):
        response = read_present(lambda: iam.get_role(RoleName=cfg["role_name"]))
        if response is not None:
            role = response["Role"]
            require_equal(role.get("Arn"), cfg["role_arn"], "role ARN")
            require_equal(role.get("RoleName"), cfg["role_name"], "role name")
            tags = {tag["Key"]: tag["Value"] for tag in role.get("Tags", [])}
            require_equal(tags.get(OWNER_TAG), owner, "role ownership tag")
            if cfg.get("role_id"):
                require_equal(role.get("RoleId"), cfg["role_id"], "role immutable ID")
