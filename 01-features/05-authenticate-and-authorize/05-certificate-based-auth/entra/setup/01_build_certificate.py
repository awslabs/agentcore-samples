"""
Build a self-signed X.509 certificate whose public key IS the AWS KMS
signing key's public half, and whose signature was produced by calling
kms:Sign. Entra ID validates JWT client assertions against a certificate
uploaded to the app registration's keyCredentials - Path B keeps the
private key entirely inside KMS at every step.

What this script does:
  1. Reads SIGNING_KMS_KEY_ARN from .env (populated by setup/00).
  2. Calls kms:GetPublicKey to fetch the DER-encoded SubjectPublicKeyInfo
     for the RSA key.
  3. Builds a v3 X.509 TBS (to-be-signed) certificate using asn1crypto:
       - Subject / Issuer  : CN=pkjwt-entra-sample
       - Serial number     : deterministic - first 8 bytes of
                             SHA-256(SPKI-DER). Stable across re-runs so
                             we don't churn keyCredential entries on Entra.
       - Validity          : 365 days from now (stable if we reuse an
                             existing cert; see idempotency below).
       - Signature algo    : sha256WithRSAEncryption (RS256)
       - Subject Public Key Info: the KMS public key
  4. Computes SHA-256 of the TBS DER bytes.
  5. Calls kms:Sign with:
       - MessageType       : DIGEST
       - SigningAlgorithm  : RSASSA_PKCS1_V1_5_SHA_256
     to produce the cert's self-signature. The KMS private key is never
     exported.
  6. Assembles the final Certificate (TBS + signatureAlgorithm +
     signatureValue) and writes it as entra_cert.pem + entra_cert.der.
  7. Computes the x5t#S256 thumbprint that Entra uses to match assertion
     JWTs to the uploaded cert, and writes X5T_S256_THUMBPRINT to .env:
       - x5t#S256 = base64url( SHA-256(cert-DER) )
     The Entra admin portal displays a SHA-1 fingerprint on its
     Certificates screen. If you want to compare it manually, run:
       openssl x509 -in entra_cert.pem -noout -fingerprint -sha1

Idempotency:
  If entra_cert.pem already exists and its embedded public key exactly
  matches the current KMS public key, the existing cert is reused - no
  new kms:Sign call is issued and the thumbprint stays the same. This
  keeps setup/02's keyCredentials-upload idempotent too.

  To force a fresh cert (e.g. after rotating the KMS key), delete
  entra_cert.pem manually or run cleanup.py first.

Usage:
    python setup/01_build_certificate.py
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import os
import re
import sys
from pathlib import Path

import boto3
from asn1crypto import algos, keys, pem, x509
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
CERT_PEM_PATH = ROOT / "entra_cert.pem"
CERT_DER_PATH = ROOT / "entra_cert.der"

# Certificate metadata. These are cosmetic - Entra doesn't validate
# subject / issuer beyond "cert is well-formed and the signature verifies".
CERT_COMMON_NAME = "pkjwt-entra-sample"
CERT_VALIDITY_DAYS = 365


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. See config.example.env.", file=sys.stderr)
        sys.exit(1)
    return value


def get_kms_public_key_der(kms, key_arn: str) -> bytes:
    """Return the DER-encoded SubjectPublicKeyInfo of a KMS asymmetric key."""
    resp = kms.get_public_key(KeyId=key_arn)
    if resp.get("KeyUsage") != "SIGN_VERIFY":
        raise RuntimeError(
            f"KMS key {key_arn} has KeyUsage={resp.get('KeyUsage')!r}, expected SIGN_VERIFY. Rerun setup/00."
        )
    return resp["PublicKey"]


def existing_cert_matches_kms(cert_pem_path: Path, kms_spki_der: bytes) -> bool:
    """Return True iff cert_pem_path exists and its SPKI equals kms_spki_der."""
    if not cert_pem_path.exists():
        return False
    try:
        _, _, cert_der = pem.unarmor(cert_pem_path.read_bytes())
        cert = x509.Certificate.load(cert_der)
        cert_spki_der = cert["tbs_certificate"]["subject_public_key_info"].dump()
        return cert_spki_der == kms_spki_der
    except (ValueError, KeyError, TypeError):
        return False


def deterministic_serial(spki_der: bytes) -> int:
    """Serial derived from the KMS SPKI - stable across re-runs."""
    return int.from_bytes(hashlib.sha256(spki_der).digest()[:8], "big") or 1


def build_tbs_certificate(spki_der: bytes) -> x509.TbsCertificate:
    """Construct the TBS block for a self-signed RSA X.509 v3 cert.

    The Subject Public Key Info section IS the KMS public key. Signing
    happens outside this function via kms:Sign on the DER of this TBS.
    """
    name = x509.Name.build({"common_name": CERT_COMMON_NAME})
    now = datetime.datetime.now(datetime.timezone.utc)
    validity = x509.Validity(
        {
            "not_before": x509.Time({"utc_time": now}),
            "not_after": x509.Time({"utc_time": now + datetime.timedelta(days=CERT_VALIDITY_DAYS)}),
        }
    )
    sig_algo = algos.SignedDigestAlgorithm({"algorithm": "sha256_rsa"})

    return x509.TbsCertificate(
        {
            "version": "v3",
            "serial_number": deterministic_serial(spki_der),
            "signature": sig_algo,
            "issuer": name,
            "validity": validity,
            "subject": name,
            "subject_public_key_info": keys.PublicKeyInfo.load(spki_der),
        }
    )


def kms_sign_tbs(kms, key_arn: str, tbs_der: bytes) -> bytes:
    """Sign the SHA-256 digest of the TBS with kms:Sign (RSASSA-PKCS1-v1_5-SHA-256)."""
    digest = hashlib.sha256(tbs_der).digest()
    resp = kms.sign(
        KeyId=key_arn,
        Message=digest,
        MessageType="DIGEST",
        SigningAlgorithm="RSASSA_PKCS1_V1_5_SHA_256",
    )
    return resp["Signature"]


def assemble_certificate(tbs: x509.TbsCertificate, signature: bytes) -> x509.Certificate:
    return x509.Certificate(
        {
            "tbs_certificate": tbs,
            "signature_algorithm": algos.SignedDigestAlgorithm({"algorithm": "sha256_rsa"}),
            "signature_value": signature,
        }
    )


def build_and_write_cert(kms, key_arn: str, spki_der: bytes) -> bytes:
    """Build the cert, write DER + PEM to disk, return the DER bytes."""
    tbs = build_tbs_certificate(spki_der)
    tbs_der = tbs.dump()
    signature = kms_sign_tbs(kms, key_arn, tbs_der)
    cert = assemble_certificate(tbs, signature)
    cert_der = cert.dump()

    CERT_DER_PATH.write_bytes(cert_der)
    CERT_PEM_PATH.write_bytes(pem.armor("CERTIFICATE", cert_der))
    return cert_der


def x5t_s256_thumbprint(cert_der: bytes) -> str:
    """Return the x5t#S256 header value: base64url(SHA-256(DER)), unpadded.

    Microsoft Entra ID uses x5t#S256 in the JWT header to match an
    assertion to the uploaded certificate. This is the modern (SHA-256)
    form; the legacy SHA-1 x5t claim is not used by AgentCore Identity's
    Entra provider. The actual crypto authentication uses RS256.

    To view the SHA-1 fingerprint that the Entra admin portal displays,
    run:
        openssl x509 -in entra_cert.pem -noout -fingerprint -sha1
    """
    sha256_bytes = hashlib.sha256(cert_der).digest()
    return base64.urlsafe_b64encode(sha256_bytes).rstrip(b"=").decode("ascii")


def write_env_var(name: str, value: str) -> None:
    text = ENV_FILE.read_text()
    pattern = rf"^{re.escape(name)}=.*$"
    replacement = f"{name}={value}"
    if re.search(pattern, text, flags=re.MULTILINE):
        new_text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
    else:
        new_text = text.rstrip() + f"\n{replacement}\n"
    ENV_FILE.write_text(new_text)
    print(f"✓ Wrote {name} to .env")


def main() -> None:
    load_dotenv(ENV_FILE)
    region = must_env("AWS_REGION")
    key_arn = must_env("SIGNING_KMS_KEY_ARN")

    kms = boto3.client("kms", region_name=region)

    print(f"Region:   {region}")
    print(f"KMS key:  {key_arn}")
    print()

    print("→ Fetching KMS public key (DER SubjectPublicKeyInfo)...")
    spki_der = get_kms_public_key_der(kms, key_arn)
    print(f"✓ Received {len(spki_der)} bytes")

    print()
    if existing_cert_matches_kms(CERT_PEM_PATH, spki_der):
        print(f"• Reusing existing cert at {CERT_PEM_PATH.name} - its public")
        print("  key already matches this KMS key. No kms:Sign call issued.")
        cert_der = (
            CERT_DER_PATH.read_bytes() if CERT_DER_PATH.exists() else (pem.unarmor(CERT_PEM_PATH.read_bytes())[2])
        )
        # Ensure the .der is present alongside the .pem for consumers.
        if not CERT_DER_PATH.exists():
            CERT_DER_PATH.write_bytes(cert_der)
            print(f"  (regenerated {CERT_DER_PATH.name} from the PEM)")
    else:
        print("→ Building X.509 TBS with the KMS public key in SPKI...")
        print("→ Calling kms:Sign to produce the certificate's self-signature")
        print("  (SigningAlgorithm=RSASSA_PKCS1_V1_5_SHA_256, MessageType=DIGEST)...")
        cert_der = build_and_write_cert(kms, key_arn, spki_der)
        print(f"✓ Wrote {CERT_PEM_PATH.name} + {CERT_DER_PATH.name}")

    x5t_s256 = x5t_s256_thumbprint(cert_der)

    print()
    write_env_var("X5T_S256_THUMBPRINT", x5t_s256)

    print()
    print("=" * 70)
    print("  Summary - step 01: KMS-signed X.509 certificate")
    print("=" * 70)
    print("  What was built (or reused):")
    print(f"    File          : {CERT_PEM_PATH.relative_to(ROOT)}")
    print(f"    Also          : {CERT_DER_PATH.relative_to(ROOT)}")
    print(f"    Subject / CN  : {CERT_COMMON_NAME}")
    print(f"    Validity      : {CERT_VALIDITY_DAYS} days from creation")
    print("    Signature alg : sha256WithRSAEncryption (RS256)")
    print("    Public key    : matches KMS key (SPKI byte-identical)")
    print()
    print("  Thumbprint - written to .env and used in setup/03 and setup/04:")
    print(f"    x5t#S256 (SHA-256 base64url) : {x5t_s256}")
    print()
    print("  To see the SHA-1 fingerprint that the Entra portal displays")
    print("  after upload (Certificates & Secrets tab), run:")
    print(f"    openssl x509 -in {CERT_PEM_PATH.name} -noout -fingerprint -sha1")
    print()
    print("  How to sanity-check the cert file yourself:")
    print(f"    openssl x509 -in {CERT_PEM_PATH.name} -noout -text | head -20")
    print(f"    openssl verify -CAfile {CERT_PEM_PATH.name} {CERT_PEM_PATH.name}")
    print("      (self-signature verifies against its own public key - 'OK')")
    print()
    print("  Written to .env:")
    print(f"    X5T_S256_THUMBPRINT={x5t_s256}")
    print()
    print("  Why this step matters:")
    print("    Entra ID does not accept a bare JWK. It requires an X.509")
    print("    certificate uploaded to the app's keyCredentials, and the")
    print("    assertion JWT header must carry the cert's thumbprint (x5t#S256)")
    print("    so Entra can match assertion → cert → app. Because the KMS")
    print("    private key never leaves KMS, the cert's TBS is signed by")
    print("    calling kms:Sign - the resulting cert has an RS256 self-")
    print("    signature just like a normal openssl-generated one.")
    print()
    print("  Next step:")
    print("    python setup/02_create_entra_service_app.py")
    print("    (creates the Entra API-services app registration via Microsoft")
    print("     Graph and uploads this certificate to its keyCredentials)")


if __name__ == "__main__":
    main()
