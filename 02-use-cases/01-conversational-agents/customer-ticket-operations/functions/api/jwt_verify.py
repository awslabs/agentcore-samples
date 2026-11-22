"""RS256 verification of Cognito access tokens with the standard library only.

RSASSA-PKCS1-v1_5 (RFC 8017 §8.2.2): s^e mod n must equal 0x00 01 FF..FF 00 || DigestInfo(SHA-256)
|| SHA-256(signing input). Keys come from the user pool's JWKS and are cached per cold start.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.request
from typing import Any

_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
_keys: dict[str, tuple[int, int]] = {}


class InvalidToken(Exception):
    pass


def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _key(issuer: str, kid: str) -> tuple[int, int]:
    if kid not in _keys:
        with urllib.request.urlopen(f"{issuer}/.well-known/jwks.json", timeout=5) as resp:  # noqa: S310  # nosec B310
            for k in json.load(resp)["keys"]:
                _keys[k["kid"]] = (
                    int.from_bytes(_b64(k["n"]), "big"),
                    int.from_bytes(_b64(k["e"]), "big"),
                )
    if kid not in _keys:
        raise InvalidToken("unknown signing key")
    return _keys[kid]


def verify(token: str, issuer: str, client_id: str) -> dict[str, Any]:
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(_b64(head_b64))
        claims = json.loads(_b64(body_b64))
        signature = _b64(sig_b64)
    except (ValueError, json.JSONDecodeError) as exc:
        raise InvalidToken("malformed token") from exc
    if header.get("alg") != "RS256":
        raise InvalidToken("unexpected algorithm")
    n, e = _key(issuer, header.get("kid", ""))
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        raise InvalidToken("bad signature")
    decoded = pow(int.from_bytes(signature, "big"), e, n).to_bytes(size, "big")
    digest = hashlib.sha256(f"{head_b64}.{body_b64}".encode()).digest()
    suffix = _SHA256_DIGEST_INFO + digest
    expected = b"\x00\x01" + b"\xff" * (size - len(suffix) - 3) + b"\x00" + suffix
    if not hmac.compare_digest(decoded, expected):
        raise InvalidToken("bad signature")
    if claims.get("iss") != issuer or claims.get("token_use") != "access":
        raise InvalidToken("wrong issuer or token use")
    if claims.get("client_id") != client_id or claims.get("exp", 0) <= time.time():
        raise InvalidToken("wrong audience or expired")
    return claims
