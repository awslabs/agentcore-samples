"""
CloudWatch-triggered polling sync for ANS metadata in AWS Agent Registry.

Lists all records in a registry, fetches fresh ANS metadata for any record
that has an ANS extension, and updates the record only if something changed.

Can be run as a standalone script:
    python sync_poller.py --registry-id <id> --interval 300
"""

import argparse
import json
import logging
import time
from datetime import datetime, timezone

from ans_metadata import ANS_EXTENSION_URI, fetch_ans_metadata, validate_ans_liveness
import registry_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def check_ans_changed(stored_ans: dict, fresh_ans: dict) -> bool:
    """Compare key ANS fields to detect meaningful changes.

    Compares:
        - status (ACTIVE vs REVOKED etc)
        - serverCert.fingerprint (cert rotation)
        - identityCert.fingerprint (version bump)
        - trustVector values (score changes)

    Does NOT trigger on syncedAt-only changes.

    Returns:
        True if something meaningful changed, False otherwise.
    """
    # Status change
    if stored_ans.get("status") != fresh_ans.get("status"):
        return True

    # Server cert fingerprint (cert rotation)
    stored_srv_fp = stored_ans.get("serverCert", {}).get("fingerprint", "")
    fresh_srv_fp = fresh_ans.get("serverCert", {}).get("fingerprint", "")
    if stored_srv_fp != fresh_srv_fp:
        return True

    # Identity cert fingerprint (version bump)
    stored_id_fp = stored_ans.get("identityCert", {}).get("fingerprint", "")
    fresh_id_fp = fresh_ans.get("identityCert", {}).get("fingerprint", "")
    if stored_id_fp != fresh_id_fp:
        return True

    # Trust vector values
    stored_tv = stored_ans.get("trustVector", {})
    fresh_tv = fresh_ans.get("trustVector", {})
    for dim in ("integrity", "identity", "solvency", "behavior", "safety"):
        if stored_tv.get(dim) != fresh_tv.get(dim):
            return True

    return False


def _extract_ans_extension(record: dict) -> tuple[dict | None, str]:
    """Extract ANS metadata from any record type.

    Supports:
        - A2A: capabilities.extensions[].params (where uri matches ANS_EXTENSION_URI)
        - MCP: x-ans-* fields at top level of server inlineContent
        - CUSTOM/ACP: ans.* nested object in inlineContent

    Returns:
        (ans_params_dict, record_format) where record_format is 'a2a', 'mcp', or 'custom'.
        Returns (None, '') if no ANS data found.
    """
    descriptors = record.get("descriptors", {})

    # Try A2A path
    try:
        a2a_raw = (
            descriptors.get("a2a", {}).get("agentCard", {}).get("inlineContent", "")
        )
        if a2a_raw:
            card = json.loads(a2a_raw)
            for ext in card.get("capabilities", {}).get("extensions", []):
                if ext.get("uri") == ANS_EXTENSION_URI:
                    return ext.get("params", {}), "a2a"
    except (json.JSONDecodeError, AttributeError):
        pass

    # Try MCP path (x-ans-* fields)
    try:
        mcp_raw = descriptors.get("mcp", {}).get("server", {}).get("inlineContent", "")
        if mcp_raw:
            server = json.loads(mcp_raw)
            if "x-ans-name" in server:
                return {
                    "ansName": server.get("x-ans-name", ""),
                    "host": server.get("x-ans-host", ""),
                    "version": server.get("x-ans-version", ""),
                    "status": server.get("x-ans-status", "UNKNOWN"),
                    "domainValidation": server.get("x-ans-domain-validation", ""),
                    "identityCert": {
                        "type": server.get("x-ans-identity-cert-type", ""),
                        "fingerprint": server.get(
                            "x-ans-identity-cert-fingerprint", ""
                        ),
                    },
                    "serverCert": {
                        "type": server.get("x-ans-server-cert-type", ""),
                        "fingerprint": server.get("x-ans-server-cert-fingerprint", ""),
                    },
                    "trustVector": {
                        "integrity": server.get("x-ans-trust-integrity", 0),
                        "identity": server.get("x-ans-trust-identity", 0),
                        "solvency": server.get("x-ans-trust-solvency", 0),
                        "behavior": server.get("x-ans-trust-behavior", 0),
                        "safety": server.get("x-ans-trust-safety", 0),
                    },
                    "trustComposite": server.get("x-ans-trust-composite", 0.0),
                    "trustProfile": server.get("x-ans-trust-profile", "UNTRUSTED"),
                    "liveness": {
                        "valid": server.get("x-ans-liveness-valid", False),
                        "dnsResolvable": server.get("x-ans-liveness-dns", False),
                        "tlBadgeReachable": server.get("x-ans-liveness-tl", False),
                        "tlsReachable": server.get("x-ans-liveness-tls", False),
                    },
                }, "mcp"
    except (json.JSONDecodeError, AttributeError):
        pass

    # Try CUSTOM/ACP path (ans.* nested object)
    try:
        custom_raw = descriptors.get("custom", {}).get("inlineContent", "")
        if custom_raw:
            content = json.loads(custom_raw)
            if "ans" in content and isinstance(content["ans"], dict):
                return content["ans"], "custom"
    except (json.JSONDecodeError, AttributeError):
        pass

    return None, ""


def _get_content_dict(record: dict) -> tuple[dict | None, str]:
    """Parse the content JSON from any record type.

    Returns:
        (content_dict, record_format) where record_format is 'a2a', 'mcp', or 'custom'.
    """
    descriptors = record.get("descriptors", {})

    # A2A
    try:
        raw = descriptors.get("a2a", {}).get("agentCard", {}).get("inlineContent", "")
        if raw:
            return json.loads(raw), "a2a"
    except (json.JSONDecodeError, AttributeError):
        pass

    # MCP
    try:
        raw = descriptors.get("mcp", {}).get("server", {}).get("inlineContent", "")
        if raw:
            return json.loads(raw), "mcp"
    except (json.JSONDecodeError, AttributeError):
        pass

    # CUSTOM
    try:
        raw = descriptors.get("custom", {}).get("inlineContent", "")
        if raw:
            return json.loads(raw), "custom"
    except (json.JSONDecodeError, AttributeError):
        pass

    return None, ""


def _build_updated_content(content: dict, fresh_ans: dict, record_format: str) -> dict:
    """Inject fresh ANS metadata into the content dict based on record format."""
    import copy
    from datetime import datetime, timezone

    updated = copy.deepcopy(content)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if record_format == "a2a":
        # Update the extension params
        for ext in updated.get("capabilities", {}).get("extensions", []):
            if ext.get("uri") == ANS_EXTENSION_URI:
                from registry_client import _build_extension_params

                ext["params"] = _build_extension_params(fresh_ans)
                break
        # Sync the agent card URL with the ANS host
        ans_host = fresh_ans.get("host", "")
        if ans_host and "url" in updated:
            from urllib.parse import urlparse, urlunparse

            parsed = urlparse(updated["url"])
            if parsed.hostname and parsed.hostname != ans_host:
                updated["url"] = urlunparse(parsed._replace(netloc=ans_host))

    elif record_format == "mcp":
        # Update x-ans-* fields
        updated["x-ans-name"] = fresh_ans.get("ansName", "")
        updated["x-ans-host"] = fresh_ans.get("host", "")
        updated["x-ans-version"] = fresh_ans.get("version", "")
        updated["x-ans-status"] = fresh_ans.get("status", "UNKNOWN")
        updated["x-ans-domain-validation"] = fresh_ans.get("domainValidation", "")
        updated["x-ans-badge-url"] = fresh_ans.get("badgeUrl", "")
        ic = fresh_ans.get("identityCert", {})
        updated["x-ans-identity-cert-type"] = ic.get("type", "")
        updated["x-ans-identity-cert-fingerprint"] = ic.get("fingerprint", "")
        sc = fresh_ans.get("serverCert", {})
        updated["x-ans-server-cert-type"] = sc.get("type", "")
        updated["x-ans-server-cert-fingerprint"] = sc.get("fingerprint", "")
        tv = fresh_ans.get("trustVector", {})
        updated["x-ans-trust-integrity"] = tv.get("integrity", 0)
        updated["x-ans-trust-identity"] = tv.get("identity", 0)
        updated["x-ans-trust-solvency"] = tv.get("solvency", 0)
        updated["x-ans-trust-behavior"] = tv.get("behavior", 0)
        updated["x-ans-trust-safety"] = tv.get("safety", 0)
        updated["x-ans-trust-composite"] = fresh_ans.get("trustComposite", 0.0)
        updated["x-ans-trust-profile"] = fresh_ans.get("trustProfile", "UNTRUSTED")
        lv = fresh_ans.get("liveness", {})
        updated["x-ans-liveness-valid"] = lv.get("valid", False)
        updated["x-ans-liveness-dns"] = lv.get("dnsResolvable", False)
        updated["x-ans-liveness-tl"] = lv.get("tlBadgeReachable", False)
        updated["x-ans-liveness-tls"] = lv.get("tlsReachable", False)
        updated["x-ans-liveness-checked-at"] = lv.get("checkedAt", now)
        updated["x-ans-synced-at"] = now

    elif record_format == "custom":
        # Update the ans nested object
        from registry_client import _build_extension_params

        updated["ans"] = _build_extension_params(fresh_ans)

    return updated


def _build_update_descriptors(updated_content: dict, record_format: str) -> dict:
    """Build the descriptors dict for update_registry_record.
    Uses optionalValue wrappers required by the boto3 API."""
    content_json = json.dumps(updated_content)
    if record_format == "a2a":
        return {
            "optionalValue": {
                "a2a": {
                    "optionalValue": {
                        "agentCard": {
                            "schemaVersion": "0.3",
                            "inlineContent": content_json,
                        }
                    }
                }
            }
        }
    elif record_format == "mcp":
        return {
            "optionalValue": {
                "mcp": {
                    "optionalValue": {
                        "server": {
                            "optionalValue": {
                                "schemaVersion": "2025-12-11",
                                "inlineContent": content_json,
                            }
                        }
                    }
                }
            }
        }
    else:
        return {
            "optionalValue": {
                "custom": {"optionalValue": {"inlineContent": content_json}}
            }
        }


def poll_and_sync(registry_id: str, region: str = "us-east-1") -> list[dict]:
    """Poll all records in a registry and sync ANS metadata where needed.

    Supports all record types:
        - A2A: ANS data in capabilities.extensions
        - MCP: ANS data in x-ans-* server fields
        - CUSTOM/ACP: ANS data in ans.* nested object

    For each record that has ANS data:
        1. Extract stored ANS params (auto-detects format)
        2. Run liveness check
        3. Fetch fresh ANS metadata from DNS + TL
        4. Compare with stored metadata
        5. Update only if something changed (format-aware update)
    """
    results = []
    records = registry_client.list_records(registry_id, region=region)
    logger.info("Found %d records in registry %s", len(records), registry_id)

    for rec_summary in records:
        record_id = rec_summary.get("recordId", rec_summary.get("registryRecordId", ""))
        record_name = rec_summary.get("name", "unknown")

        try:
            full_record = registry_client.get_record(
                registry_id, record_id, region=region
            )
        except Exception as e:
            results.append(
                {
                    "record_id": record_id,
                    "record_name": record_name,
                    "ans_name": "",
                    "record_format": "",
                    "changed": False,
                    "updated": False,
                    "error": str(e),
                }
            )
            continue

        stored_ans, record_format = _extract_ans_extension(full_record)
        if not stored_ans:
            continue

        ans_name = stored_ans.get("ansName", "")
        if not ans_name:
            continue

        sync_result = {
            "record_id": record_id,
            "record_name": record_name,
            "ans_name": ans_name,
            "record_format": record_format,
            "changed": False,
            "updated": False,
            "liveness_valid": None,
            "liveness_checks": [],
            "error": None,
        }

        try:
            liveness = validate_ans_liveness(ans_name)
            sync_result["liveness_valid"] = liveness["valid"]
            sync_result["liveness_checks"] = liveness["checks"]

            if not liveness["valid"]:
                logger.warning(
                    "⚠️  ANS liveness FAILED for %s [%s] (%s): %s",
                    record_name,
                    record_format,
                    ans_name,
                    liveness["error"],
                )
                sync_result["error"] = f"Liveness failed: {liveness['error']}"

            fresh_ans = fetch_ans_metadata(ans_name)
            changed = check_ans_changed(stored_ans, fresh_ans)
            sync_result["changed"] = changed

            if changed:
                content, fmt = _get_content_dict(full_record)
                if content:
                    updated_content = _build_updated_content(content, fresh_ans, fmt)
                    descriptors = _build_update_descriptors(updated_content, fmt)

                    cp = registry_client._cp_client(region)
                    cp.update_registry_record(
                        registryId=registry_id,
                        recordId=record_id,
                        descriptors=descriptors,
                    )
                    sync_result["updated"] = True
                    logger.info(
                        "✅ Updated [%s] %s (%s)", fmt.upper(), record_name, ans_name
                    )
                else:
                    sync_result["error"] = "Could not parse record content"
            else:
                logger.info(
                    "No changes for [%s] %s (%s) — liveness: %s",
                    record_format,
                    record_name,
                    ans_name,
                    "VALID" if liveness["valid"] else "INVALID",
                )
        except Exception as e:
            sync_result["error"] = str(e)
            logger.error("Error syncing [%s] %s: %s", record_format, record_name, e)

        results.append(sync_result)

    return results


def run_polling_loop(
    registry_id: str,
    interval_seconds: int = 300,
    region: str = "us-east-1",
):
    """Continuous polling loop that calls poll_and_sync every N seconds.

    Args:
        registry_id: Registry to poll.
        interval_seconds: Seconds between polls.
        region: AWS region.
    """
    logger.info(
        "Starting ANS sync polling loop: registry=%s, interval=%ds, region=%s",
        registry_id,
        interval_seconds,
        region,
    )
    while True:
        try:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            logger.info("--- Poll cycle at %s ---", ts)
            results = poll_and_sync(registry_id, region=region)

            updated_count = sum(1 for r in results if r.get("updated"))
            checked_count = len(results)
            logger.info(
                "Poll complete: %d records checked, %d updated",
                checked_count,
                updated_count,
            )
        except Exception as e:
            logger.error("Poll cycle error: %s", e)

        logger.info("Sleeping %ds until next poll...", interval_seconds)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="ANS metadata sync poller for AWS Agent Registry"
    )
    parser.add_argument("--registry-id", required=True, help="AWS Agent Registry ID")
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Poll interval in seconds (default: 300)",
    )
    parser.add_argument(
        "--region", default="us-east-1", help="AWS region (default: us-east-1)"
    )
    args = parser.parse_args()

    run_polling_loop(args.registry_id, args.interval, args.region)
