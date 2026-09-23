"""Generate an RSA keypair for private_key_jwt client authentication.

Writes the private key as PEM (used by the agent / test script to sign client
assertions) and prints the public key as a JWK to register:
  * on the Okta app (Sign On -> Client Credentials -> Public keys), and
  * with the resource app (RESOURCE_CLIENT_PUBLIC_KEYS).

Usage:
  python3 gen_keypair.py --out-dir ./keys --kid xaa-agent-1
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _rfc7638_thumbprint(n: str, e: str) -> str:
    canonical = json.dumps({"e": e, "kty": "RSA", "n": n}, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="./keys")
    parser.add_argument(
        "--name",
        default="key",
        help="Label for this key set, e.g. 'okta' or 'resource'. "
        "Produces <name>_private_key.pem and <name>_public_jwk.json.",
    )
    parser.add_argument("--kid", default=None, help="Key ID (defaults to RFC 7638 thumbprint)")
    parser.add_argument("--key-size", type=int, default=2048)
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=args.key_size)
    public_numbers = private_key.public_key().public_numbers()

    n = _b64url_uint(public_numbers.n)
    e = _b64url_uint(public_numbers.e)
    kid = args.kid or _rfc7638_thumbprint(n, e)

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    private_path = out / f"{args.name}_private_key.pem"
    private_path.write_bytes(private_pem)

    public_jwk = {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid, "n": n, "e": e}
    jwk_path = out / f"{args.name}_public_jwk.json"
    jwk_path.write_text(json.dumps(public_jwk, indent=2))

    prefix = args.name.upper()
    print(f"Private key : {private_path}")
    print(f"Public JWK  : {jwk_path}")
    print(f"kid         : {kid}\n")
    print("Register this public JWK with the relevant party, then set:")
    print("  CLIENT_AUTH_METHOD=private_key_jwt")
    print(f"  {prefix}_PRIVATE_KEY_PATH={private_path}")
    print(f"  {prefix}_PRIVATE_KEY_KID={kid}\n")
    print("Public JWK:")
    print(json.dumps(public_jwk, indent=2))


if __name__ == "__main__":
    main()
