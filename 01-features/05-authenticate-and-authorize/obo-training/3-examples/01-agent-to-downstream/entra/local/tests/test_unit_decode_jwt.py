"""Unit tests for JWT decode helper in 02_run_example.py.

These tests do not hit any network or AWS service.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


from tests.conftest import make_jwt


def _load_run_example():
    """Import 02_run_example.py (module name is not a valid Python identifier)."""
    path = Path(__file__).resolve().parent.parent / "02_run_example.py"
    spec = importlib.util.spec_from_file_location("run_example", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestDecodeJwtClaims:
    def test_decodes_standard_payload(self):
        run_example = _load_run_example()
        claims = {"aud": "graph", "sub": "user-alice", "scp": "User.Read"}
        token = make_jwt(claims)

        decoded = run_example.decode_jwt_claims(token)

        assert decoded == claims

    def test_handles_padded_base64(self):
        """JWT base64 has no padding; ensure we add it back correctly."""
        run_example = _load_run_example()
        claims = {"x": "short"}  # produces base64 length divisible by 4
        token = make_jwt(claims)
        assert run_example.decode_jwt_claims(token) == claims

        claims2 = {"x": "xx"}
        token2 = make_jwt(claims2)
        assert run_example.decode_jwt_claims(token2) == claims2

    def test_malformed_token_returns_error(self):
        run_example = _load_run_example()
        decoded = run_example.decode_jwt_claims("not-a-jwt")
        assert "_decode_error" in decoded
