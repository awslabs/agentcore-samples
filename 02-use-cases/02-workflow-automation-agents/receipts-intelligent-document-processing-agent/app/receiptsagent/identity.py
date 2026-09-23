"""Tamper-proof caller identity for the conversational query path.

The IDOR fix. A user must NOT be able to read another user's expenses by editing the
`user_id` in the request body. So in query mode the agent never trusts the body — it
derives `user_id` ONLY from a signed identity token whose signature it verifies.

Mechanism: **KMS HMAC** (`GenerateMac`/`VerifyMac`). The signing key never leaves KMS,
so nothing client-side — not even the agent or a prompt-injected tool call — can forge
a token. The trusted invoker (the chat client) mints a token binding `{user_id, exp}`;
the agent verifies the MAC and the expiry and uses the bound `user_id`.

Token format (compact, URL-safe): `<b64url(claim_json)>.<b64url(mac_bytes)>`.

Threats this stops:
- Swap the body `user_id`  -> ignored in query mode; only the verified token is read.
- Forge / tamper a token    -> VerifyMac fails -> rejected.
- Replay an expired token   -> exp check fails -> rejected.
- Steal the signing key     -> it's in KMS; GenerateMac/VerifyMac are the only access.
"""

import base64
import json
import time
from typing import Any

MAC_ALGORITHM = "HMAC_SHA_256"
DEFAULT_TTL_SECONDS = 900  # 15 min — a query token is short-lived


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def encode_claim(user_id: str, expires_at: int) -> bytes:
    """Pure: the exact bytes that get MAC'd. Shared by sign + verify so they agree."""
    return json.dumps({"user_id": user_id, "exp": int(expires_at)}, separators=(",", ":"), sort_keys=True).encode()


def assemble_token(claim_bytes: bytes, mac_bytes: bytes) -> str:
    return f"{_b64u(claim_bytes)}.{_b64u(mac_bytes)}"


def split_token(token: str) -> tuple[bytes, bytes]:
    """Pure: split a token into (claim_bytes, mac_bytes). Raises ValueError if malformed."""
    parts = token.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("malformed identity token")
    return _b64u_decode(parts[0]), _b64u_decode(parts[1])


def claim_is_valid(claim_bytes: bytes, now: int) -> dict[str, Any]:
    """Pure: parse the claim and enforce expiry. Returns the claim dict or raises."""
    claim = json.loads(claim_bytes)
    if "user_id" not in claim or "exp" not in claim:
        raise ValueError("identity claim missing fields")
    if int(claim["exp"]) < now:
        raise ValueError("identity token expired")
    return claim


def sign_identity(
    user_id: str, key_id: str, region: str, ttl: int = DEFAULT_TTL_SECONDS, now: int | None = None
) -> str:
    """Mint a signed identity token (the trusted invoker / chat client side)."""
    import boto3

    now = int(now if now is not None else time.time())
    claim = encode_claim(user_id, now + ttl)
    mac = boto3.client("kms", region_name=region).generate_mac(KeyId=key_id, MacAlgorithm=MAC_ALGORITHM, Message=claim)[
        "Mac"
    ]
    return assemble_token(claim, mac)


def verify_identity(token: str, key_id: str, region: str, now: int | None = None) -> str:
    """Verify a token and return the bound user_id — the ONLY trusted source of the
    caller's identity in query mode. Raises on any tamper/expiry/verification failure
    (fail closed: a bad token must NOT resolve to a user). The key never leaves KMS."""
    import boto3

    now = int(now if now is not None else time.time())
    claim_bytes, mac_bytes = split_token(token)
    resp = boto3.client("kms", region_name=region).verify_mac(
        KeyId=key_id, MacAlgorithm=MAC_ALGORITHM, Message=claim_bytes, Mac=mac_bytes
    )
    if not resp.get("MacValid"):
        raise ValueError("identity token signature invalid")
    claim = claim_is_valid(claim_bytes, now)  # enforce expiry only after MAC is valid
    return str(claim["user_id"])
