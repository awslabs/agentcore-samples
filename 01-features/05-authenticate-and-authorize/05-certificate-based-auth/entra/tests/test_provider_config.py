"""
Offline unit tests for the Entra PRIVATE_KEY_JWT sample setup scripts.

Covers:
  - KMS key policy shape (setup/00)
  - X.509 TBS construction + thumbprint math (setup/01)
  - Provider config for M2M (setup/03) - RS256, x5t#S256 header, no OBO
  - Provider config for OBO (setup/04) - same shape + JWT_AUTHORIZATION_GRANT
  - Provider config for USER_FEDERATION (setup/04b) - different client_id, no OBO
  - App-role UUID determinism (setup/02a)

No AWS credentials, Entra tenant, `az` session, or Microsoft Graph
access required. Run with:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
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


# ─────────────────────────── KMS policy ─────────────────────────────


class KmsKeyPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_setup_module("00_provision_signing_key.py", "entra_provision_key")

    def test_policy_has_admin_and_service_statements(self) -> None:
        policy = json.loads(self.mod.build_key_policy("123456789012"))
        sids = [s["Sid"] for s in policy["Statement"]]
        self.assertIn("EnableKeyAdmin", sids)
        self.assertIn("BedrockAgentCoreIdentityPrivateKeyJwtAccess", sids)

    def test_service_statement_grants_sign_and_getpublickey_and_describe(self) -> None:
        policy = json.loads(self.mod.build_key_policy("123456789012"))
        service = next(s for s in policy["Statement"] if s["Sid"] == "BedrockAgentCoreIdentityPrivateKeyJwtAccess")
        # setup/01 needs kms:GetPublicKey (to build the cert's SPKI) and
        # kms:Sign (to sign the cert's TBS). Runtime uses the same three.
        self.assertEqual(
            sorted(service["Action"]),
            sorted(["kms:Sign", "kms:GetPublicKey", "kms:DescribeKey"]),
        )
        self.assertEqual(
            service["Condition"]["StringLike"]["kms:ViaService"],
            "bedrock-agentcore-identity.*.amazonaws.com",
        )

    def test_all_statements_have_principal(self) -> None:
        policy = json.loads(self.mod.build_key_policy("123456789012"))
        for stmt in policy["Statement"]:
            self.assertIn("Principal", stmt, f"{stmt['Sid']} missing Principal")
            self.assertEqual(stmt["Principal"]["AWS"], "arn:aws:iam::123456789012:root")


# ─────────────────────────── Cert builder ───────────────────────────


class CertificateBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from asn1crypto import x509
            from cryptography.hazmat.primitives.asymmetric import rsa
        except ImportError:
            raise unittest.SkipTest("cryptography or asn1crypto not installed")
        cls.mod = load_setup_module("01_build_certificate.py", "entra_build_cert")

        # Build a synthetic RSA SPKI to feed the module - avoids AWS calls.
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

        cls._priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls._spki_der = cls._priv.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)

    def test_deterministic_serial_is_stable(self) -> None:
        # Two calls with the same SPKI should produce the same serial.
        a = self.mod.deterministic_serial(self._spki_der)
        b = self.mod.deterministic_serial(self._spki_der)
        self.assertEqual(a, b)
        self.assertGreater(a, 0)

    def test_deterministic_serial_changes_with_spki(self) -> None:
        # A different SPKI should yield a different serial.
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_spki = other.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        self.assertNotEqual(
            self.mod.deterministic_serial(self._spki_der),
            self.mod.deterministic_serial(other_spki),
        )

    def test_tbs_carries_kms_spki_unchanged(self) -> None:
        tbs = self.mod.build_tbs_certificate(self._spki_der)
        tbs_spki_der = tbs["subject_public_key_info"].dump()
        self.assertEqual(tbs_spki_der, self._spki_der)

    def test_tbs_signature_algorithm_is_sha256_rsa(self) -> None:
        tbs = self.mod.build_tbs_certificate(self._spki_der)
        # asn1crypto exposes algorithm oid names - "sha256_rsa" is what
        # we set in build_tbs_certificate; the value is that string.
        self.assertEqual(tbs["signature"]["algorithm"].native, "sha256_rsa")

    def test_tbs_version_is_v3(self) -> None:
        tbs = self.mod.build_tbs_certificate(self._spki_der)
        self.assertEqual(tbs["version"].native, "v3")

    def test_x5t_s256_thumbprint_is_correct_encoding(self) -> None:
        # Build a full cert (TBS + fake signature) to feed the thumbprint fn.
        tbs = self.mod.build_tbs_certificate(self._spki_der)
        fake_sig = b"x" * 256
        cert = self.mod.assemble_certificate(tbs, fake_sig)
        cert_der = cert.dump()

        x5t_s256 = self.mod.x5t_s256_thumbprint(cert_der)

        # x5t#S256 = base64url(SHA-256(cert-DER)), unpadded
        expected_x5t_s256 = base64.urlsafe_b64encode(hashlib.sha256(cert_der).digest()).rstrip(b"=").decode("ascii")
        self.assertEqual(x5t_s256, expected_x5t_s256)
        self.assertNotIn("=", x5t_s256)
        self.assertNotIn("+", x5t_s256)
        self.assertNotIn("/", x5t_s256)


# ─────────────────────────── Provider configs ───────────────────────


class M2MProviderConfigTests(unittest.TestCase):
    """setup/03_create_provider_m2m.py: build_provider_config."""

    TENANT = "11111111-2222-3333-4444-555555555555"
    CLIENT = "66666666-7777-8888-9999-aaaaaaaaaaaa"
    KMS_ARN = "arn:aws:kms:us-west-2:111122223333:key/abc-123"
    X5T = "testthumbprintxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_setup_module("03_create_provider_m2m.py", "entra_m2m_provider")
        cls.config = cls.mod.build_provider_config(
            tenant_id=cls.TENANT,
            client_id=cls.CLIENT,
            kms_key_arn=cls.KMS_ARN,
            x5t_s256=cls.X5T,
        )
        cls.inner = cls.config["customOauth2ProviderConfig"]

    def test_uses_custom_oauth2_shape(self) -> None:
        self.assertIn("customOauth2ProviderConfig", self.config)

    def test_client_auth_method_is_private_key_jwt(self) -> None:
        self.assertEqual(self.inner["clientAuthenticationMethod"], "PRIVATE_KEY_JWT")

    def test_no_client_secret(self) -> None:
        self.assertNotIn("clientSecret", self.inner)

    def test_discovery_url_is_entra_v2(self) -> None:
        self.assertEqual(
            self.inner["oauthDiscovery"]["discoveryUrl"],
            f"https://login.microsoftonline.com/{self.TENANT}/v2.0/.well-known/openid-configuration",
        )

    def test_signing_algorithm_rs256(self) -> None:
        self.assertEqual(self.inner["privateKeyJwtConfig"]["signingAlgorithm"], "RS256")

    def test_header_claim_is_x5t_s256(self) -> None:
        pk = self.inner["privateKeyJwtConfig"]
        # The header claim key must be exactly "x5t#S256" (hash in key name).
        self.assertEqual(pk["additionalHeaderClaims"], {"x5t#S256": self.X5T})
        self.assertNotIn("kid", pk["additionalHeaderClaims"])
        self.assertNotIn("x5t", pk["additionalHeaderClaims"])

    def test_no_additional_payload_claims(self) -> None:
        # Entra defaults `aud` to the discovered token endpoint, which
        # matches what it wants. No override needed - leaving this
        # unset avoids surprising an Entra rules engine that inspects
        # the assertion payload.
        pk = self.inner["privateKeyJwtConfig"]
        self.assertNotIn("additionalPayloadClaims", pk)

    def test_no_obo_config(self) -> None:
        self.assertNotIn("onBehalfOfTokenExchangeConfig", self.inner)

    def test_private_key_source_uses_kms_arn(self) -> None:
        self.assertEqual(
            self.inner["privateKeyJwtConfig"]["privateKeySource"],
            {"kmsKeySource": {"kmsKeyArn": self.KMS_ARN}},
        )


class OBOProviderConfigTests(unittest.TestCase):
    """setup/04_create_provider_obo.py: build_provider_config."""

    TENANT = "11111111-2222-3333-4444-555555555555"
    CLIENT = "66666666-7777-8888-9999-aaaaaaaaaaaa"
    KMS_ARN = "arn:aws:kms:us-west-2:111122223333:key/abc-123"
    X5T = "testthumbprintxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_setup_module("04_create_provider_obo.py", "entra_obo_provider")
        cls.config = cls.mod.build_provider_config(
            tenant_id=cls.TENANT,
            client_id=cls.CLIENT,
            kms_key_arn=cls.KMS_ARN,
            x5t_s256=cls.X5T,
        )
        cls.inner = cls.config["customOauth2ProviderConfig"]

    def test_shares_shape_with_m2m(self) -> None:
        self.assertEqual(self.inner["clientAuthenticationMethod"], "PRIVATE_KEY_JWT")
        self.assertNotIn("clientSecret", self.inner)
        self.assertEqual(self.inner["privateKeyJwtConfig"]["signingAlgorithm"], "RS256")
        self.assertEqual(
            self.inner["privateKeyJwtConfig"]["additionalHeaderClaims"],
            {"x5t#S256": self.X5T},
        )

    def test_obo_grant_type_is_jwt_authorization_grant(self) -> None:
        obo = self.inner["onBehalfOfTokenExchangeConfig"]
        self.assertEqual(obo["grantType"], "JWT_AUTHORIZATION_GRANT")

    def test_obo_has_no_token_exchange_subblock(self) -> None:
        # JWT_AUTHORIZATION_GRANT does not use tokenExchangeGrantTypeConfig.
        obo = self.inner["onBehalfOfTokenExchangeConfig"]
        self.assertNotIn("tokenExchangeGrantTypeConfig", obo)

    def test_obo_has_no_actor_token_content(self) -> None:
        # Entra doesn't accept an actor_token - actorTokenContent is
        # RFC 8693 territory (Okta), never Entra.
        obo = self.inner["onBehalfOfTokenExchangeConfig"]
        self.assertNotIn("actorTokenContent", obo)


class ClientProviderConfigTests(unittest.TestCase):
    """setup/04b_create_provider_client.py: build_provider_config."""

    TENANT = "11111111-2222-3333-4444-555555555555"
    WEB_CLIENT = "bbbbbbbb-1111-1111-1111-111111111111"
    KMS_ARN = "arn:aws:kms:us-west-2:111122223333:key/abc-123"
    X5T = "testthumbprintxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_setup_module("04b_create_provider_client.py", "entra_client_provider")
        cls.config = cls.mod.build_provider_config(
            tenant_id=cls.TENANT,
            client_id=cls.WEB_CLIENT,
            kms_key_arn=cls.KMS_ARN,
            x5t_s256=cls.X5T,
        )
        cls.inner = cls.config["customOauth2ProviderConfig"]

    def test_uses_web_apps_client_id(self) -> None:
        self.assertEqual(self.inner["clientId"], self.WEB_CLIENT)

    def test_client_auth_method_is_private_key_jwt(self) -> None:
        self.assertEqual(self.inner["clientAuthenticationMethod"], "PRIVATE_KEY_JWT")

    def test_header_claim_is_x5t_s256(self) -> None:
        pk = self.inner["privateKeyJwtConfig"]
        self.assertEqual(pk["additionalHeaderClaims"], {"x5t#S256": self.X5T})

    def test_no_obo_config(self) -> None:
        # The client provider is used for authorization_code exchange
        # (USER_FEDERATION), NOT for OBO. It should not carry
        # onBehalfOfTokenExchangeConfig.
        self.assertNotIn("onBehalfOfTokenExchangeConfig", self.inner)


# ─────────────────────────── App role UUID ──────────────────────────


class AppRoleUuidTests(unittest.TestCase):
    """setup/02a_configure_entra_permissions.py: stable_role_uuid."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_setup_module("02a_configure_entra_permissions.py", "entra_permissions")

    def test_same_inputs_yield_same_uuid(self) -> None:
        a = self.mod.stable_role_uuid("client-id-1", "m2m")
        b = self.mod.stable_role_uuid("client-id-1", "m2m")
        self.assertEqual(a, b)

    def test_different_role_names_differ(self) -> None:
        a = self.mod.stable_role_uuid("client-id-1", "m2m")
        b = self.mod.stable_role_uuid("client-id-1", "admin")
        self.assertNotEqual(a, b)

    def test_different_client_ids_differ(self) -> None:
        a = self.mod.stable_role_uuid("client-id-1", "m2m")
        b = self.mod.stable_role_uuid("client-id-2", "m2m")
        self.assertNotEqual(a, b)

    def test_result_is_uuid_string(self) -> None:
        import uuid

        result = self.mod.stable_role_uuid("some-client", "some-role")
        # Should be parseable as a UUID.
        parsed = uuid.UUID(result)
        self.assertEqual(str(parsed), result)

    def test_scope_uuid_differs_from_role_uuid(self) -> None:
        # stable_scope_uuid must be namespaced differently from
        # stable_role_uuid so an app role and a delegated scope with
        # the same name don't collide.
        role = self.mod.stable_role_uuid("client-id-1", "obo_access")
        scope = self.mod.stable_scope_uuid("client-id-1", "obo_access")
        self.assertNotEqual(role, scope)

    def test_scope_uuid_is_stable(self) -> None:
        a = self.mod.stable_scope_uuid("client-id-1", "obo_access")
        b = self.mod.stable_scope_uuid("client-id-1", "obo_access")
        self.assertEqual(a, b)


# ─────────────────────────── keyCredential body ─────────────────────


class KeyCredentialBodyTests(unittest.TestCase):
    """setup/02_create_entra_service_app.py: build_key_credential."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_setup_module("02_create_entra_service_app.py", "entra_create_service_app")

    def test_shape_matches_graph_keycredential_resource(self) -> None:
        cert_der = b"fake DER bytes for a certificate"
        kc = self.mod.build_key_credential(cert_der)

        # Fields Graph expects.
        self.assertEqual(kc["type"], "AsymmetricX509Cert")
        self.assertEqual(kc["usage"], "Verify")

        # customKeyIdentifier = base64(SHA-256(cert-DER))
        expected_id = base64.b64encode(hashlib.sha256(cert_der).digest()).decode("ascii")
        self.assertEqual(kc["customKeyIdentifier"], expected_id)

        # key = base64(cert-DER)
        self.assertEqual(base64.b64decode(kc["key"]), cert_der)


if __name__ == "__main__":
    unittest.main()
