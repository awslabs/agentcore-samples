"""Generate the Okta AI Agent's private_key_jwt keypair.

You register the PUBLIC JWK on the AI Agent in Okta and keep the private key.
The interceptor signs its client assertion with the private half for both ID-JAG
legs.

    python scripts/gen_keypair.py                    # RSA 2048, kid xaa-agent
    python scripts/gen_keypair.py --kid my-key-1

Writes scripts/keys/okta_private_key.pem (0600) and okta_public_jwk.json.
Both are gitignored; the directory and *.pem are covered.

Treat the keypair as IMMUTABLE once registered. Re-running this after
registering the public key breaks every signature with
`invalid_client: client_assertion signature is invalid`, and Okta will not let
you deactivate an agent's only key or reuse a kid. To rotate: add the new public
key under a NEW kid, repoint AI_AGENT_KEY_KID, then deactivate the old one.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

KEYS_DIR = Path(__file__).resolve().parent / "keys"


def b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kid", default="xaa-agent", help="Key id to register in Okta.")
    ap.add_argument("--bits", type=int, default=2048)
    ap.add_argument("--force", action="store_true", help="Overwrite an existing keypair.")
    args = ap.parse_args()

    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    priv_path = KEYS_DIR / "okta_private_key.pem"
    jwk_path = KEYS_DIR / "okta_public_jwk.json"

    if priv_path.exists() and not args.force:
        print(f"  {priv_path.relative_to(Path.cwd())} already exists -- keeping it.")
        print("  Keys are immutable once registered in Okta; pass --force only if")
        print("  you have not registered this public key yet.")
        print(f"  public JWK: {jwk_path}")
        sys.exit(0)

    key = rsa.generate_private_key(public_exponent=65537, key_size=args.bits)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    priv_path.write_bytes(pem)
    os.chmod(priv_path, 0o600)

    numbers = key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": args.kid,
        "use": "sig",
        "alg": "RS256",
        "n": b64url_uint(numbers.n),
        "e": b64url_uint(numbers.e),
    }
    jwk_path.write_text(json.dumps(jwk, indent=2) + "\n")

    print(f"  ✓ private key: {priv_path}  (mode 0600, gitignored)")
    print(f"  ✓ public JWK:  {jwk_path}")
    print(f"  ✓ kid:         {args.kid}   -> set AI_AGENT_KEY_KID={args.kid} in .env")
    print()
    print("  Next: register the AI Agent in the Okta Admin Console and paste the")
    print("  contents of okta_public_jwk.json as its public key. See IDP_SETUP_OKTA.md.")


if __name__ == "__main__":
    main()
