"""
Offline unit tests for the Okta PRIVATE_KEY_JWT sample setup scripts.

These tests import the `build_provider_config` function from each setup
script and assert the resulting boto3 payload has the correct shape. They
also verify the KMS-DER-to-JWK conversion and .env update helpers.

No AWS credentials or Okta tenant required. Run with:
    python -m unittest discover -s tests -v

The tests intentionally use stdlib-only assertions and importlib to load
the numeric-prefixed scripts (which are not importable via a normal
`import` statement).
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETUP_DIR = HERE.parent / "setup"


def load_setup_module(filename: str, mod_name: str):
    """Load a numeric-prefixed setup script as an importable module."""
    path = SETUP_DIR / filename
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


class M2MProviderConfigTests(unittest.TestCase):
    """setup/02_create_provider_m2m.py: build_provider_config."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_setup_module("02_create_provider_m2m.py", "okta_m2m_provider")
        cls.config = cls.mod.build_provider_config(
            discovery_url="https://example.okta.com/oauth2/default/.well-known/openid-configuration",
            client_id="0oatest-client",
            kms_key_arn="arn:aws:kms:us-west-2:111122223333:key/abc-123",
            kid="testkid1234567890",
        )
        cls.inner = cls.config["customOauth2ProviderConfig"]

    def test_uses_custom_oauth2_shape(self) -> None:
        self.assertIn("customOauth2ProviderConfig", self.config)

    def test_client_auth_method_is_private_key_jwt(self) -> None:
        self.assertEqual(self.inner["clientAuthenticationMethod"], "PRIVATE_KEY_JWT")

    def test_no_client_secret(self) -> None:
        self.assertNotIn("clientSecret", self.inner)

    def test_private_key_jwt_block_populated(self) -> None:
        pk = self.inner["privateKeyJwtConfig"]
        self.assertEqual(
            pk["privateKeySource"]["kmsKeySource"]["kmsKeyArn"],
            "arn:aws:kms:us-west-2:111122223333:key/abc-123",
        )
        self.assertEqual(pk["signingAlgorithm"], "RS256")

    def test_additional_header_claims_use_kid(self) -> None:
        pk = self.inner["privateKeyJwtConfig"]
        self.assertEqual(pk["additionalHeaderClaims"], {"kid": "testkid1234567890"})

    def test_no_obo_config(self) -> None:
        self.assertNotIn("onBehalfOfTokenExchangeConfig", self.inner)


class OBOProviderConfigTests(unittest.TestCase):
    """setup/03_create_provider_obo.py: build_provider_config."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_setup_module("03_create_provider_obo.py", "okta_obo_provider")
        cls.config = cls.mod.build_provider_config(
            discovery_url="https://example.okta.com/oauth2/default/.well-known/openid-configuration",
            client_id="0oatest-client",
            kms_key_arn="arn:aws:kms:us-west-2:111122223333:key/abc-123",
            kid="testkid1234567890",
        )
        cls.inner = cls.config["customOauth2ProviderConfig"]

    def test_shares_shape_with_m2m(self) -> None:
        self.assertEqual(self.inner["clientAuthenticationMethod"], "PRIVATE_KEY_JWT")
        self.assertNotIn("clientSecret", self.inner)
        self.assertEqual(self.inner["privateKeyJwtConfig"]["signingAlgorithm"], "RS256")
        self.assertEqual(
            self.inner["privateKeyJwtConfig"]["additionalHeaderClaims"],
            {"kid": "testkid1234567890"},
        )

    def test_obo_grant_type_is_token_exchange(self) -> None:
        obo = self.inner["onBehalfOfTokenExchangeConfig"]
        self.assertEqual(obo["grantType"], "TOKEN_EXCHANGE")

    def test_actor_token_content_is_none(self) -> None:
        obo = self.inner["onBehalfOfTokenExchangeConfig"]
        self.assertEqual(obo["tokenExchangeGrantTypeConfig"]["actorTokenContent"], "NONE")


class JwkConversionTests(unittest.TestCase):
    """setup/01_create_okta_service_app.py: kms_public_key_to_jwk + b64url_uint."""

    @classmethod
    def setUpClass(cls) -> None:
        # This test module needs the cryptography lib.
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives.serialization import (
                Encoding,
                PublicFormat,
            )
        except ImportError:
            raise unittest.SkipTest("cryptography not installed")

        cls.mod = load_setup_module("01_create_okta_service_app.py", "okta_create_app")

        # Generate a real RSA_2048 key so we exercise the same DER path
        # as the KMS response.
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.der = cls.private_key.public_key().public_bytes(
            encoding=Encoding.DER, format=PublicFormat.SubjectPublicKeyInfo
        )

    def test_jwk_kty_alg_use(self) -> None:
        jwk = self.mod.kms_public_key_to_jwk(self.der)
        self.assertEqual(jwk["kty"], "RSA")
        self.assertEqual(jwk["alg"], "RS256")
        self.assertEqual(jwk["use"], "sig")

    def test_jwk_kid_is_deterministic_16char_hex(self) -> None:
        jwk1 = self.mod.kms_public_key_to_jwk(self.der)
        jwk2 = self.mod.kms_public_key_to_jwk(self.der)
        self.assertEqual(jwk1["kid"], jwk2["kid"])
        self.assertEqual(len(jwk1["kid"]), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in jwk1["kid"]))

    def test_jwk_n_and_e_populated_and_base64url_unpadded(self) -> None:
        jwk = self.mod.kms_public_key_to_jwk(self.der)
        for part in ("n", "e"):
            self.assertIn(part, jwk)
            self.assertNotIn("=", jwk[part], f"{part} has b64 padding")
            self.assertTrue(
                all(c.isalnum() or c in "-_" for c in jwk[part]),
                f"{part} is not base64url",
            )

    def test_b64url_uint_helper(self) -> None:
        # Small known-value smoke tests: 65537 = 0x010001 → "AQAB".
        self.assertEqual(self.mod.b64url_uint(65537), "AQAB")
        # 1 → "AQ" (padding stripped)
        self.assertEqual(self.mod.b64url_uint(1), "AQ")

    def test_non_rsa_key_raises(self) -> None:
        try:
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives.serialization import (
                Encoding,
                PublicFormat,
            )
        except ImportError:
            self.skipTest("cryptography EC support unavailable")

        ec_key = ec.generate_private_key(ec.SECP256R1())
        ec_der = ec_key.public_key().public_bytes(encoding=Encoding.DER, format=PublicFormat.SubjectPublicKeyInfo)
        with self.assertRaises(TypeError):
            self.mod.kms_public_key_to_jwk(ec_der)


class DcrPayloadTests(unittest.TestCase):
    """setup/01_create_okta_service_app.py: DCR payload contains JWKS inline."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa
        except ImportError:
            raise unittest.SkipTest("cryptography not installed")
        cls.mod = load_setup_module("01_create_okta_service_app.py", "okta_create_app_dcr")

    def test_dcr_endpoint_is_used_and_jwks_is_inline(self) -> None:
        # Verify the source explicitly targets the /oauth2/v1/clients DCR
        # endpoint rather than /api/v1/apps, and that the JWKS is included
        # inline in the payload. Both are load-bearing choices - the
        # regression the last blog-team review flagged came from using
        # /api/v1/apps for a private_key_jwt app.
        from pathlib import Path

        src = Path(self.mod.__file__).read_text()
        self.assertIn("/oauth2/v1/clients", src)
        self.assertIn('"jwks": {"keys": [jwk]}', src)
        self.assertIn("token_endpoint_auth_method", src)


if __name__ == "__main__":
    unittest.main()
