"""
Fetch ANS metadata from DNS + Transparency Log and compute trust vectors.

Uses real DNS queries (dnspython) and HTTPS (requests) to gather live
ANS identity, certificate, and trust data for a given ANS name.
"""

import hashlib
import re
import socket
import ssl
from datetime import datetime, timezone

import dns.resolver
import requests


ANS_EXTENSION_URI = "https://ans-protocol.org/ext/ans-identity/v1"
TL_BASE_URL = "https://transparency.ans.godaddy.com"


def _create_tls_context() -> ssl.SSLContext:
    """Create a secure TLS context enforcing TLS 1.2 minimum."""
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class AnsLivenessError(Exception):
    """Raised when an ANS name fails liveness validation."""

    pass


def parse_ans_name(ans_name: str) -> tuple[str, str]:
    """Parse an ANS name into (version, host).

    Example: ans://v1.0.0.support-abc.helpagent.club -> ("1.0.0", "support-abc.helpagent.club")
    """
    stripped = re.sub(r"^ans://", "", ans_name)
    match = re.match(r"v(\d+\.\d+\.\d+)\.(.*)", stripped)
    if match:
        return match.group(1), match.group(2)
    return "0.0.0", stripped


def parse_txt_fields(txt_records: list[str]) -> dict:
    """Parse key=value pairs from DNS TXT record strings."""
    fields = {}
    for record in txt_records:
        for part in record.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                fields[k.strip()] = v.strip()
    return fields


def validate_ans_liveness(ans_name: str) -> dict:
    """Validate that an ANS name is live and valid.

    Checks:
        1. ANS name format is valid (ans://v{version}.{host})
        2. DNS _ans-badge TXT record exists (agent is registered)
        3. Transparency Log badge is reachable and returns ACTIVE status
        4. TLS connection to the agent host succeeds (agent is reachable)
        5. Badge status is not REVOKED or EXPIRED

    Returns:
        dict with keys:
            valid: bool — overall pass/fail
            checks: list of {check, passed, detail} dicts
            status: str — TL badge status if available
            error: str | None — first failure reason

    Raises:
        AnsLivenessError: If the ANS name is invalid or not live (when
            called via validate_ans_liveness_or_raise).
    """
    version, host = parse_ans_name(ans_name)
    checks = []
    overall_valid = True
    first_error = None
    tl_status = None

    # Check 1: Format
    format_ok = bool(
        version != "0.0.0" and host and "." in host and ans_name.startswith("ans://")
    )
    checks.append(
        {
            "check": "ANS name format",
            "passed": format_ok,
            "detail": f"version={version}, host={host}"
            if format_ok
            else "Invalid ANS name format",
        }
    )
    if not format_ok:
        overall_valid = False
        first_error = first_error or f"Invalid ANS name format: {ans_name}"

    # Check 2: DNS _ans-badge exists
    badge_url = ""
    try:
        badge_txt = dns.resolver.resolve(f"_ans-badge.{host}", "TXT")
        badge_raw = [r.to_text().strip('"') for r in badge_txt]
        badge_fields = parse_txt_fields(badge_raw)
        badge_url = badge_fields.get("url", "")
        checks.append(
            {
                "check": "DNS _ans-badge TXT",
                "passed": True,
                "detail": f"Found: {badge_raw[0][:80]}...",
            }
        )
    except dns.resolver.NXDOMAIN:
        checks.append(
            {
                "check": "DNS _ans-badge TXT",
                "passed": False,
                "detail": f"NXDOMAIN — no _ans-badge record for {host}",
            }
        )
        overall_valid = False
        first_error = (
            first_error
            or f"No _ans-badge DNS record for {host} — agent not registered in ANS"
        )
    except Exception as e:
        checks.append(
            {
                "check": "DNS _ans-badge TXT",
                "passed": False,
                "detail": f"DNS error: {e}",
            }
        )
        overall_valid = False
        first_error = first_error or f"DNS lookup failed for _ans-badge.{host}: {e}"

    # Check 3: TL badge reachable and ACTIVE
    if badge_url:
        try:
            r = requests.get(badge_url, timeout=10)
            r.raise_for_status()
            tl_data = r.json()
            tl_status = tl_data.get("status", "UNKNOWN")
            is_valid_status = tl_status in ("ACTIVE", "WARNING", "DEPRECATED")
            checks.append(
                {
                    "check": "Transparency Log badge",
                    "passed": is_valid_status,
                    "detail": f"Status: {tl_status}",
                }
            )
            if not is_valid_status:
                overall_valid = False
                first_error = (
                    first_error
                    or f"ANS agent status is {tl_status} — not valid for registration"
                )
        except Exception as e:
            checks.append(
                {
                    "check": "Transparency Log badge",
                    "passed": False,
                    "detail": f"Fetch failed: {e}",
                }
            )
            overall_valid = False
            first_error = (
                first_error or f"Cannot reach Transparency Log at {badge_url}: {e}"
            )
    else:
        if overall_valid:  # Only add this check if we expected a badge URL
            checks.append(
                {
                    "check": "Transparency Log badge",
                    "passed": False,
                    "detail": "No badge URL found",
                }
            )

    # Check 4: TLS reachable
    try:
        ctx = _create_tls_context()
        with ctx.wrap_socket(
            socket.create_connection((host, 443), timeout=5),
            server_hostname=host,
        ) as s:
            s.getpeercert(binary_form=True)
            checks.append(
                {
                    "check": "TLS reachability",
                    "passed": True,
                    "detail": f"Connected to {host}:443 with valid TLS",
                }
            )
    except Exception as e:
        checks.append(
            {
                "check": "TLS reachability",
                "passed": False,
                "detail": f"Cannot connect to {host}:443: {e}",
            }
        )
        overall_valid = False
        first_error = first_error or f"Agent at {host}:443 is not reachable: {e}"

    return {
        "valid": overall_valid,
        "checks": checks,
        "status": tl_status,
        "error": first_error,
    }


def validate_ans_liveness_or_raise(ans_name: str) -> dict:
    """Validate ANS liveness and raise AnsLivenessError if invalid.

    Returns the liveness result dict on success.
    """
    result = validate_ans_liveness(ans_name)
    if not result["valid"]:
        raise AnsLivenessError(result["error"])
    return result


def fetch_ans_metadata(ans_name: str) -> dict:
    """Fetch full ANS metadata for an agent from DNS and Transparency Log.

    Args:
        ans_name: Full ANS name, e.g. "ans://v1.0.0.support-abc.helpagent.club"

    Returns:
        Structured dict with all ANS fields ready for embedding in an A2A extension.
    """
    version, host = parse_ans_name(ans_name)

    result = {
        "ansName": ans_name,
        "host": host,
        "version": version,
        "status": "UNKNOWN",
        "domainValidation": "",
        "registeredAt": "",
        "badgeUrl": "",
        "identityCert": {"type": "", "fingerprint": ""},
        "serverCert": {"type": "", "fingerprint": ""},
        "trustVector": {
            "integrity": 0,
            "identity": 0,
            "solvency": 0,
            "behavior": 0,
            "safety": 0,
        },
        "trustComposite": 0.0,
        "trustProfile": "UNTRUSTED",
        "syncedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # --- DNS: _ans TXT record (protocol + endpoint) ---
    try:
        ans_txt = dns.resolver.resolve(f"_ans.{host}", "TXT")
        ans_raw = [r.to_text().strip('"') for r in ans_txt]
        parse_txt_fields(ans_raw)
        # Store any useful fields from _ans TXT (protocol, endpoint, etc.)
    except Exception:
        pass  # _ans TXT may not exist for all agents

    # --- DNS: _ans-badge TXT record (badge URL) ---
    badge_url = ""
    try:
        badge_txt = dns.resolver.resolve(f"_ans-badge.{host}", "TXT")
        badge_raw = [r.to_text().strip('"') for r in badge_txt]
        badge_fields = parse_txt_fields(badge_raw)
        badge_url = badge_fields.get("url", "")
        result["badgeUrl"] = badge_url
    except Exception:
        pass

    # --- HTTPS: Transparency Log badge ---
    if badge_url:
        try:
            r = requests.get(badge_url, timeout=10)
            r.raise_for_status()
            tl = r.json()
            event = tl.get("payload", {}).get("producer", {}).get("event", {})
            att = event.get("attestations", {})

            result["status"] = tl.get("status", "UNKNOWN")
            result["domainValidation"] = att.get("domainValidation", "")
            result["registeredAt"] = event.get("issuedAt", "")

            id_cert = att.get("identityCert", {})
            result["identityCert"] = {
                "type": id_cert.get("type", ""),
                "fingerprint": id_cert.get("fingerprint", ""),
            }

            srv_cert = att.get("serverCert", {})
            result["serverCert"] = {
                "type": srv_cert.get("type", ""),
                "fingerprint": srv_cert.get("fingerprint", ""),
            }
        except Exception:
            pass

    # --- TLS: Live server cert fingerprint for PKI validation ---
    try:
        ctx = _create_tls_context()
        with ctx.wrap_socket(
            socket.create_connection((host, 443), timeout=5),
            server_hostname=host,
        ) as s:
            cert_der = s.getpeercert(binary_form=True)
            fp = hashlib.sha256(cert_der).hexdigest()
            # If we didn't get a server cert fingerprint from TL, use the live one
            if not result["serverCert"]["fingerprint"]:
                result["serverCert"]["fingerprint"] = f"SHA256:{fp}"
                result["serverCert"]["type"] = "X509-DV-SERVER"
    except Exception:
        pass

    # --- Compute trust vector ---
    signals = _gather_trust_signals(host, badge_url, result)
    trust = compute_trust_vector(signals)
    result["trustVector"] = trust["vector"]
    result["trustComposite"] = trust["composite"]
    result["trustProfile"] = trust["profile"]

    # Add liveness metadata
    liveness = validate_ans_liveness(ans_name)
    result["liveness"] = {
        "valid": liveness["valid"],
        "checks": liveness["checks"],
        "checkedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    return result


def _gather_trust_signals(host: str, badge_url: str, metadata: dict) -> dict:
    """Gather trust signals from DNS, TLS, and TL data for scoring."""
    signals: dict = {}

    # TL badge signals
    signals["has_badge"] = metadata.get("status") not in ("UNKNOWN", "", None)
    signals["tl_status"] = metadata.get("status")
    signals["id_cert_type"] = metadata.get("identityCert", {}).get("type", "")
    signals["srv_cert_type"] = metadata.get("serverCert", {}).get("type", "")
    signals["domain_validation"] = metadata.get("domainValidation", "")

    # A2A Agent Card
    try:
        r = requests.get(f"https://{host}/.well-known/agent.json", timeout=10)
        signals["has_agent_card"] = r.ok
    except Exception:
        signals["has_agent_card"] = False

    # DNS TLSA (DANE)
    try:
        dns.resolver.resolve(f"_443._tcp.{host}", "TLSA")
        signals["has_tlsa"] = True
    except Exception:
        signals["has_tlsa"] = False

    # DNS HTTPS (SVCB)
    try:
        dns.resolver.resolve(host, "HTTPS")
        signals["has_https"] = True
    except Exception:
        signals["has_https"] = False

    # PKI validation
    try:
        ctx = _create_tls_context()
        with ctx.wrap_socket(
            socket.create_connection((host, 443), timeout=5),
            server_hostname=host,
        ) as s:
            s.getpeercert(binary_form=True)
            signals["pki_valid"] = True
    except Exception:
        signals["pki_valid"] = False

    return signals


def compute_trust_vector(signals: dict) -> dict:
    """Compute the 5-dimension trust vector from gathered signals.

    Scoring logic matches ans-registry/web/server.py step_trust endpoint.

    Returns:
        dict with keys: vector, profile, composite
    """
    # --- Integrity ---
    integrity = 45  # base
    if signals.get("has_badge"):
        integrity += 15
    if signals.get("has_agent_card"):
        integrity += 10
    if signals.get("has_tlsa"):
        integrity += 15
    if signals.get("pki_valid"):
        integrity += 10

    # --- Identity ---
    identity = 30  # base (domain validated)
    id_cert = signals.get("id_cert_type", "")
    if "EV" in id_cert:
        identity += 35
    elif "OV" in id_cert:
        identity += 20

    srv_cert = signals.get("srv_cert_type", "")
    if "EV" in srv_cert:
        identity += 20
    elif "OV" in srv_cert:
        identity += 10

    vector = {
        "integrity": min(integrity, 100),
        "identity": min(identity, 100),
        "solvency": 0,
        "behavior": 50,
        "safety": 40,
    }

    avg = sum(vector.values()) / len(vector)
    if avg >= 90:
        profile = "FIDUCIARY"
    elif avg >= 70:
        profile = "TRANSACTIONAL"
    elif avg >= 50:
        profile = "READ_ONLY"
    else:
        profile = "UNTRUSTED"

    return {
        "vector": vector,
        "profile": profile,
        "composite": round(avg, 1),
    }
