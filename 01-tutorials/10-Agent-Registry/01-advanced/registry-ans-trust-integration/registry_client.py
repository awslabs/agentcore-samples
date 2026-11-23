"""
AWS Agent Registry client wrapper.

Wraps boto3 bedrock-agentcore-control (CRUD) and bedrock-agentcore (search)
clients for managing registries and A2A records with ANS extensions.
"""

import json
import time
import logging

import boto3
from botocore.exceptions import ClientError

from ans_metadata import ANS_EXTENSION_URI
from ans_metadata import AnsLivenessError, validate_ans_liveness_or_raise

# Re-export for consumers
__all__ = ["AnsNameConflictError", "AnsVersionMismatchError", "AnsLivenessError"]

logger = logging.getLogger(__name__)


def _cp_client(region: str = "us-east-1"):
    """Create a control-plane client."""
    return boto3.client("bedrock-agentcore-control", region_name=region)


def _dp_client(region: str = "us-east-1"):
    """Create a data-plane client (search)."""
    return boto3.client("bedrock-agentcore", region_name=region)


# ---------------------------------------------------------------------------
# ANS Name Validation — uniqueness + version alignment
# ---------------------------------------------------------------------------


class AnsNameConflictError(Exception):
    """Raised when an ANS name already exists in the registry."""

    pass


class AnsVersionMismatchError(Exception):
    """Raised when the ANS version doesn't match the record version."""

    pass


def _extract_ans_name_from_content(content_json: str) -> str | None:
    """Extract the ANS name from record content JSON.

    Checks:
        1. A2A/CUSTOM: capabilities.extensions[].params.ansName
        2. MCP: x-ans-name field at top level
        3. ACP/CUSTOM: ans.ansName
    """
    try:
        card = json.loads(content_json)
        # A2A extensions path
        for ext in card.get("capabilities", {}).get("extensions", []):
            if ext.get("uri") == ANS_EXTENSION_URI:
                return ext.get("params", {}).get("ansName")
        # MCP x-ans-name path
        if "x-ans-name" in card:
            return card["x-ans-name"]
        # ACP/CUSTOM ans.ansName path
        if "ans" in card and isinstance(card["ans"], dict):
            return card["ans"].get("ansName")
    except (json.JSONDecodeError, AttributeError):
        pass
    return None


def _extract_ans_version_from_name(ans_name: str) -> str | None:
    """Extract the version from an ANS name.

    ans://v1.0.0.host.example.com -> '1.0.0'
    """
    import re

    m = re.match(r"ans://v(\d+\.\d+\.\d+)\.", ans_name)
    return m.group(1) if m else None


def _get_content_from_record(record: dict) -> str:
    """Extract the inlineContent from a record's descriptors."""
    desc = record.get("descriptors", {})
    # Try A2A first, then custom
    a2a = desc.get("a2a", {})
    if a2a:
        return a2a.get("agentCard", {}).get("inlineContent", "")
    custom = desc.get("custom", {})
    return custom.get("inlineContent", "")


def check_ans_name_unique(
    registry_id: str,
    ans_name: str,
    exclude_record_id: str | None = None,
    region: str = "us-east-1",
) -> None:
    """Check that no other record in the registry has the same ANS name.

    Args:
        registry_id: Registry to check.
        ans_name: The ANS name to validate uniqueness for.
        exclude_record_id: Record ID to exclude (for updates to self).
        region: AWS region.

    Raises:
        AnsNameConflictError: If another record already has this ANS name.
    """
    if not ans_name:
        return

    cp = _cp_client(region)
    records = cp.list_registry_records(registryId=registry_id).get(
        "registryRecords", []
    )

    for rec in records:
        record_id = rec.get("recordId", "")
        if exclude_record_id and record_id == exclude_record_id:
            continue

        try:
            full = cp.get_registry_record(registryId=registry_id, recordId=record_id)
            content = _get_content_from_record(full)
            existing_ans = _extract_ans_name_from_content(content)
            if existing_ans and existing_ans == ans_name:
                raise AnsNameConflictError(
                    f"ANS name '{ans_name}' already exists in record "
                    f"'{rec.get('name', record_id)}' ({record_id}). "
                    f"ANS name must be unique per registry."
                )
        except (ClientError, KeyError):
            continue


def validate_ans_version_alignment(ans_name: str, record_version: str) -> None:
    """Validate that the ANS version matches the record version.

    The version in the ANS name (e.g., v1.0.0 in ans://v1.0.0.host.com)
    must match the recordVersion field in the AWS Agent Registry record.

    Args:
        ans_name: The full ANS name.
        record_version: The recordVersion from the registry record.

    Raises:
        AnsVersionMismatchError: If versions don't match.
    """
    if not ans_name:
        return

    ans_version = _extract_ans_version_from_name(ans_name)
    if not ans_version:
        return

    # Normalize: strip leading 'v' from record_version if present
    normalized_record = record_version.lstrip("v").strip()
    normalized_ans = ans_version.strip()

    if normalized_record != normalized_ans:
        raise AnsVersionMismatchError(
            f"ANS version '{ans_version}' (from '{ans_name}') does not match "
            f"record version '{record_version}'. They must be aligned. "
            f"If the agent was upgraded, create a new record with the new version."
        )


class AnsHostMismatchError(Exception):
    """Raised when the agent card URL host doesn't match the ANS host."""

    pass


def validate_ans_host_alignment(content_json: str, ans_name: str) -> None:
    """Validate that the agent card URL host matches the ANS host.

    The host in the agent card's `url` field must match the host
    extracted from the ANS name. This ensures the registry record
    points to the same endpoint that ANS has verified.

    Args:
        content_json: The agent card JSON string.
        ans_name: The full ANS name.

    Raises:
        AnsHostMismatchError: If hosts don't match.
    """
    if not ans_name or not content_json:
        return

    from urllib.parse import urlparse

    _, ans_host = _extract_ans_version_from_name(ans_name), None
    # Extract host from ANS name
    import re

    m = re.match(r"ans://v[\d.]+\.(.*)", ans_name)
    if not m:
        return
    ans_host = m.group(1)

    # Extract host from agent card URL
    try:
        card = json.loads(content_json)
        card_url = card.get("url", "")
        if not card_url:
            return
        parsed = urlparse(card_url)
        card_host = parsed.hostname or ""
    except (json.JSONDecodeError, AttributeError):
        return

    if not card_host or not ans_host:
        return

    if card_host.lower() != ans_host.lower():
        raise AnsHostMismatchError(
            f"Agent card URL host '{card_host}' does not match ANS host '{ans_host}' "
            f"(from '{ans_name}'). The registry record must point to the same "
            f"endpoint that ANS has verified."
        )


# ---------------------------------------------------------------------------
# Registry CRUD
# ---------------------------------------------------------------------------


def create_registry(name: str, description: str, region: str = "us-east-1") -> str:
    """Create a new Agent Registry with IAM auth and auto-approval.

    Returns:
        registry_id
    """
    cp = _cp_client(region)
    resp = cp.create_registry(
        name=name,
        description=description,
        approvalConfiguration={"autoApproval": True},
    )
    registry_arn = resp["registryArn"]
    registry_id = registry_arn.split("/")[-1]
    logger.info("Created registry %s (ARN: %s)", registry_id, registry_arn)

    # Wait for READY
    _wait_for_registry_ready(cp, registry_id)
    return registry_id


def _wait_for_registry_ready(cp, registry_id: str, timeout: int = 120):
    """Poll until registry reaches READY status."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = cp.get_registry(registryId=registry_id)
        status = r["status"]
        logger.info("  Registry %s status: %s", registry_id, status)
        if status == "READY":
            return r
        time.sleep(3)
    raise TimeoutError(f"Registry {registry_id} not READY after {timeout}s")


# ---------------------------------------------------------------------------
# Record CRUD
# ---------------------------------------------------------------------------


def create_record_generic(
    registry_id: str,
    name: str,
    description: str,
    descriptor_type: str,
    descriptors: dict,
    version: str = "1.0.0",
    region: str = "us-east-1",
) -> str:
    """Create a record of any type (MCP, A2A, CUSTOM, AGENT_SKILLS).

    Validates ANS name uniqueness and version alignment if the content
    contains an ANS extension.

    Raises:
        AnsNameConflictError: If another record has the same ANS name.
        AnsVersionMismatchError: If ANS version doesn't match record version.
    """
    # Extract content to check for ANS extension
    content = ""
    dt = descriptor_type.upper()
    if dt == "A2A":
        content = (
            descriptors.get("a2a", {}).get("agentCard", {}).get("inlineContent", "")
        )
    elif dt == "CUSTOM":
        content = descriptors.get("custom", {}).get("inlineContent", "")

    if content:
        ans_name = _extract_ans_name_from_content(content)
        if ans_name:
            validate_ans_liveness_or_raise(ans_name)
            check_ans_name_unique(registry_id, ans_name, region=region)
            validate_ans_version_alignment(ans_name, version)
            validate_ans_host_alignment(content, ans_name)

    cp = _cp_client(region)
    resp = cp.create_registry_record(
        registryId=registry_id,
        name=name,
        description=description,
        descriptorType=descriptor_type,
        descriptors=descriptors,
        recordVersion=version,
    )
    record_arn = resp["recordArn"]
    record_id = record_arn.split("/")[-1]
    logger.info(
        "Created %s record %s in registry %s", descriptor_type, record_id, registry_id
    )
    _wait_for_record_ready(cp, registry_id, record_id)
    return record_id


def create_agent_record(
    registry_id: str,
    name: str,
    description: str,
    agent_card_with_ans_extension: str,
    version: str = "1.0.0",
    region: str = "us-east-1",
) -> str:
    """Create an A2A record with an agent card that includes the ANS extension.

    Validates:
        - ANS name uniqueness across the registry
        - ANS version matches the record version

    Raises:
        AnsNameConflictError: If another record has the same ANS name.
        AnsVersionMismatchError: If ANS version doesn't match record version.
    """
    # Validate ANS name uniqueness and version alignment
    ans_name = _extract_ans_name_from_content(agent_card_with_ans_extension)
    if ans_name:
        validate_ans_liveness_or_raise(ans_name)
        check_ans_name_unique(registry_id, ans_name, region=region)
        validate_ans_version_alignment(ans_name, version)
        validate_ans_host_alignment(agent_card_with_ans_extension, ans_name)

    cp = _cp_client(region)
    resp = cp.create_registry_record(
        registryId=registry_id,
        name=name,
        description=description,
        descriptorType="A2A",
        descriptors={
            "a2a": {
                "agentCard": {
                    "schemaVersion": "0.3",
                    "inlineContent": agent_card_with_ans_extension,
                }
            }
        },
        recordVersion=version,
    )
    record_arn = resp["recordArn"]
    record_id = record_arn.split("/")[-1]
    logger.info("Created record %s in registry %s", record_id, registry_id)

    _wait_for_record_ready(cp, registry_id, record_id)
    return record_id


def _wait_for_record_ready(cp, registry_id: str, record_id: str, timeout: int = 120):
    """Poll until record exits CREATING/UPDATING status."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = cp.get_registry_record(registryId=registry_id, recordId=record_id)
        status = resp["status"]
        logger.info("  Record %s status: %s", record_id, status)
        if status not in ("CREATING", "UPDATING"):
            return resp
        time.sleep(3)
    raise TimeoutError(f"Record {record_id} still not ready after {timeout}s")


def get_record(registry_id: str, record_id: str, region: str = "us-east-1") -> dict:
    """Get full record details."""
    cp = _cp_client(region)
    return cp.get_registry_record(registryId=registry_id, recordId=record_id)


def update_record_ans_metadata(
    registry_id: str,
    record_id: str,
    current_card: dict,
    new_ans_metadata: dict,
    region: str = "us-east-1",
) -> bool:
    """Update the ANS extension params in the agent card.

    Validates:
        - If ANS name changed, the new name must be unique
        - ANS version must still match the record version

    Returns:
        True if the record was updated, False if no change detected.

    Raises:
        AnsNameConflictError: If the new ANS name conflicts with another record.
    """
    # Find existing ANS extension
    extensions = current_card.get("capabilities", {}).get("extensions", [])
    existing_params = None
    for ext in extensions:
        if ext.get("uri") == ANS_EXTENSION_URI:
            existing_params = ext.get("params", {})
            break

    # Build new params from fresh metadata
    new_params = _build_extension_params(new_ans_metadata)

    # If ANS name changed (version bump), check uniqueness
    old_ans_name = existing_params.get("ansName", "") if existing_params else ""
    new_ans_name = new_params.get("ansName", "")
    if new_ans_name and new_ans_name != old_ans_name:
        check_ans_name_unique(
            registry_id, new_ans_name, exclude_record_id=record_id, region=region
        )

    # Compare meaningful fields (skip syncedAt)
    if existing_params and not _params_changed(existing_params, new_params):
        logger.info(
            "No ANS metadata changes for record %s — skipping update", record_id
        )
        return False

    # Update the extension in the card
    updated_card = _inject_ans_extension(current_card, new_params)
    updated_json = json.dumps(updated_card)

    cp = _cp_client(region)
    cp.update_registry_record(
        registryId=registry_id,
        recordId=record_id,
        descriptors={
            "a2a": {
                "agentCard": {
                    "schemaVersion": "0.3",
                    "inlineContent": updated_json,
                }
            }
        },
    )
    logger.info("Updated ANS metadata for record %s", record_id)
    return True


def _params_changed(old: dict, new: dict) -> bool:
    """Check if meaningful ANS params changed (ignoring syncedAt)."""
    # Compare status
    if old.get("status") != new.get("status"):
        return True
    # Compare cert fingerprints
    if old.get("serverCert", {}).get("fingerprint") != new.get("serverCert", {}).get(
        "fingerprint"
    ):
        return True
    if old.get("identityCert", {}).get("fingerprint") != new.get(
        "identityCert", {}
    ).get("fingerprint"):
        return True
    # Compare trust vector values
    old_tv = old.get("trustVector", {})
    new_tv = new.get("trustVector", {})
    for dim in ("integrity", "identity", "solvency", "behavior", "safety"):
        if old_tv.get(dim) != new_tv.get(dim):
            return True
    return False


def _build_extension_params(ans_metadata: dict) -> dict:
    """Build the params dict for the ANS extension from metadata.

    Includes liveness and validity indicators directly in the stored metadata.
    """
    from datetime import datetime, timezone

    liveness = ans_metadata.get("liveness", {})
    liveness_checks = liveness.get("checks", [])

    return {
        "ansName": ans_metadata.get("ansName", ""),
        "host": ans_metadata.get("host", ""),
        "version": ans_metadata.get("version", ""),
        "status": ans_metadata.get("status", "UNKNOWN"),
        "domainValidation": ans_metadata.get("domainValidation", ""),
        "registeredAt": ans_metadata.get("registeredAt", ""),
        "badgeUrl": ans_metadata.get("badgeUrl", ""),
        "identityCert": ans_metadata.get("identityCert", {}),
        "serverCert": ans_metadata.get("serverCert", {}),
        "trustVector": ans_metadata.get("trustVector", {}),
        "trustComposite": ans_metadata.get("trustComposite", 0.0),
        "trustProfile": ans_metadata.get("trustProfile", "UNTRUSTED"),
        # Liveness and validity indicators
        "liveness": {
            "valid": liveness.get("valid", False),
            "checkedAt": liveness.get(
                "checkedAt", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            ),
            "dnsResolvable": next(
                (
                    c["passed"]
                    for c in liveness_checks
                    if c["check"] == "DNS _ans-badge TXT"
                ),
                False,
            ),
            "tlBadgeReachable": next(
                (
                    c["passed"]
                    for c in liveness_checks
                    if c["check"] == "Transparency Log badge"
                ),
                False,
            ),
            "tlsReachable": next(
                (
                    c["passed"]
                    for c in liveness_checks
                    if c["check"] == "TLS reachability"
                ),
                False,
            ),
            "formatValid": next(
                (
                    c["passed"]
                    for c in liveness_checks
                    if c["check"] == "ANS name format"
                ),
                False,
            ),
        },
        "syncedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def build_ans_metadata_for_mcp(ans_metadata: dict) -> dict:
    """Build ANS metadata dict suitable for embedding in MCP server schema custom fields.

    Returns a flat dict that can be merged into the MCP server JSON.
    """
    params = _build_extension_params(ans_metadata)
    return {
        "x-ans-name": params.get("ansName", ""),
        "x-ans-host": params.get("host", ""),
        "x-ans-version": params.get("version", ""),
        "x-ans-status": params.get("status", "UNKNOWN"),
        "x-ans-domain-validation": params.get("domainValidation", ""),
        "x-ans-badge-url": params.get("badgeUrl", ""),
        "x-ans-identity-cert-type": params.get("identityCert", {}).get("type", ""),
        "x-ans-identity-cert-fingerprint": params.get("identityCert", {}).get(
            "fingerprint", ""
        ),
        "x-ans-server-cert-type": params.get("serverCert", {}).get("type", ""),
        "x-ans-server-cert-fingerprint": params.get("serverCert", {}).get(
            "fingerprint", ""
        ),
        "x-ans-trust-integrity": params.get("trustVector", {}).get("integrity", 0),
        "x-ans-trust-identity": params.get("trustVector", {}).get("identity", 0),
        "x-ans-trust-solvency": params.get("trustVector", {}).get("solvency", 0),
        "x-ans-trust-behavior": params.get("trustVector", {}).get("behavior", 0),
        "x-ans-trust-safety": params.get("trustVector", {}).get("safety", 0),
        "x-ans-trust-composite": params.get("trustComposite", 0.0),
        "x-ans-trust-profile": params.get("trustProfile", "UNTRUSTED"),
        "x-ans-liveness-valid": params.get("liveness", {}).get("valid", False),
        "x-ans-liveness-dns": params.get("liveness", {}).get("dnsResolvable", False),
        "x-ans-liveness-tl": params.get("liveness", {}).get("tlBadgeReachable", False),
        "x-ans-liveness-tls": params.get("liveness", {}).get("tlsReachable", False),
        "x-ans-liveness-checked-at": params.get("liveness", {}).get("checkedAt", ""),
        "x-ans-synced-at": params.get("syncedAt", ""),
    }


def build_acp_record_content(
    agent_name: str,
    agent_description: str,
    ans_metadata: dict,
    commerce_credentials: list[dict] | None = None,
) -> dict:
    """Build a CUSTOM record content for ACP (Agentic Commerce Protocol).

    Combines agent identity (from ANS) with commerce credentials
    (Visa TAP, Mastercard Agent Pay, Forter TACP, x402, etc.).

    Args:
        agent_name: Human-readable agent name.
        agent_description: What the agent does.
        ans_metadata: ANS metadata from fetch_ans_metadata().
        commerce_credentials: List of commerce credential dicts, each with:
            - network: str (e.g., "visa-tap", "mastercard-agent-pay", "forter-tacp", "x402")
            - status: str (e.g., "REGISTERED", "ACTIVE", "SUSPENDED")
            - registeredAt: str (ISO timestamp)
            - credentialId: str (network-specific ID)
            - capabilities: list[str] (e.g., ["browse", "purchase", "refund"])

    Returns:
        dict suitable for json.dumps() as CUSTOM inlineContent.
    """
    ans_params = _build_extension_params(ans_metadata)

    return {
        "protocolType": "ACP",
        "protocolVersion": "0.1.0",
        "name": agent_name,
        "description": agent_description,
        "ans": ans_params,
        "commerceCredentials": commerce_credentials or [],
        "supportedNetworks": [c["network"] for c in (commerce_credentials or [])],
    }


def _inject_ans_extension(card: dict, params: dict) -> dict:
    """Inject or replace the ANS extension in an agent card dict."""
    import copy

    updated = copy.deepcopy(card)
    caps = updated.setdefault("capabilities", {})
    extensions = caps.setdefault("extensions", [])

    # Replace existing or append
    found = False
    for ext in extensions:
        if ext.get("uri") == ANS_EXTENSION_URI:
            ext["params"] = params
            found = True
            break
    if not found:
        extensions.append(
            {
                "uri": ANS_EXTENSION_URI,
                "description": "ANS public identity, trust verification, and trust scores",
                "required": False,
                "params": params,
            }
        )
    return updated


# ---------------------------------------------------------------------------
# Approval workflow
# ---------------------------------------------------------------------------


def submit_for_approval(registry_id: str, record_id: str, region: str = "us-east-1"):
    """Submit a DRAFT record for approval (DRAFT -> PENDING_APPROVAL)."""
    cp = _cp_client(region)
    cp.submit_registry_record_for_approval(registryId=registry_id, recordId=record_id)
    logger.info("Submitted record %s for approval", record_id)


def approve_record(
    registry_id: str,
    record_id: str,
    reason: str = "Approved",
    region: str = "us-east-1",
):
    """Approve a PENDING_APPROVAL record (PENDING_APPROVAL -> APPROVED)."""
    cp = _cp_client(region)
    cp.update_registry_record_status(
        registryId=registry_id,
        recordId=record_id,
        status="APPROVED",
        statusReason=reason,
    )
    logger.info("Approved record %s: %s", record_id, reason)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def search_records(registry_id: str, query: str, region: str = "us-east-1") -> list:
    """Semantic search for records in a registry via the data plane.

    Args:
        registry_id: Registry ID to search.
        query: Natural language search query.
        region: AWS region.

    Returns:
        List of matching record dicts.
    """
    dp = _dp_client(region)
    # The search API needs the registry ARN
    sts = boto3.client("sts", region_name=region)
    account_id = sts.get_caller_identity()["Account"]
    registry_arn = (
        f"arn:aws:bedrock-agentcore:{region}:{account_id}:registry/{registry_id}"
    )

    resp = dp.search_registry_records(
        registryIds=[registry_arn],
        searchQuery=query,
        maxResults=10,
    )
    return resp.get("registryRecords", [])


def list_records(registry_id: str, region: str = "us-east-1") -> list:
    """List all records in a registry."""
    cp = _cp_client(region)
    resp = cp.list_registry_records(registryId=registry_id)
    return resp.get("registryRecords", [])


# ---------------------------------------------------------------------------
# Helper: Build agent card with ANS extension
# ---------------------------------------------------------------------------


def build_agent_card_with_ans(agent_card_dict: dict, ans_metadata: dict) -> str:
    """Build an A2A agent card JSON string with the ANS extension embedded.

    Args:
        agent_card_dict: Base A2A agent card as a Python dict.
        ans_metadata: ANS metadata dict from fetch_ans_metadata().

    Returns:
        JSON string of the agent card with ANS extension in capabilities.extensions.
    """
    params = _build_extension_params(ans_metadata)
    updated = _inject_ans_extension(agent_card_dict, params)
    return json.dumps(updated)
