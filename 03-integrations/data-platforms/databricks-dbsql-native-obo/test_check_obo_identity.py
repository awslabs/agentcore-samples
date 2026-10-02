"""Tests for check_obo_identity.

Run with either:
    python -m unittest discover -v
    pytest -v
"""

import base64
import contextlib
import io
import json
import os
import unittest
from unittest import mock

import check_obo_identity
from check_obo_identity import (
    CALLER_PERMISSIONS,
    EXCHANGE_REFUSED,
    EXIT_CODES,
    GATEWAY_UNREACHABLE,
    IDENTITY_MISMATCH,
    INBOUND_TOKEN,
    PER_USER,
    PER_USER_UNVERIFIED,
    PROVIDER_NOT_FOUND,
    REMEDY_PER_USER,
    REMEDY_PER_USER_UNCHECKED,
    REMEDY_SUBJECT_NOT_COMPARABLE,
    SERVICE_PRINCIPAL,
    TARGET_NOT_FOUND,
    TARGET_REJECTED_TOKEN,
    TRANSIENT,
    UNKNOWN,
    WORKSPACE_MEMBERSHIP,
    GatewayUnreachable,
    classify_failure,
    classify_identity,
    extract_identity,
    extract_text,
    is_error,
    main,
    mcp_post,
    parse_mcp_body,
    qualified_tool_name,
    resolve_identity,
    run_check,
    subject_from_token,
)


def _jwt(payload: dict) -> str:
    """Build an unsigned token with the given payload. Nothing verifies it; the script only reads it."""

    def segment(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{segment({'alg': 'none'})}.{segment(payload)}.signature"


class ClassifyIdentity(unittest.TestCase):
    def test_email_is_per_user(self):
        self.assertEqual(classify_identity("someone@example.com"), PER_USER)

    def test_application_uuid_is_service_principal(self):
        self.assertEqual(classify_identity("00000000-0000-4000-8000-000000000000"), SERVICE_PRINCIPAL)

    def test_uppercase_uuid_is_service_principal(self):
        # Must contain uppercase hex letters, or it cannot exercise re.IGNORECASE on _UUID.
        self.assertEqual(classify_identity("A1B2C3D4-5E6F-4A7B-8C9D-0E1F2A3B4C5D"), SERVICE_PRINCIPAL)

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(classify_identity("  someone@example.com  "), PER_USER)

    def test_none_and_empty_are_unknown(self):
        self.assertEqual(classify_identity(None), UNKNOWN)
        self.assertEqual(classify_identity(""), UNKNOWN)
        self.assertEqual(classify_identity("   "), UNKNOWN)

    def test_bare_word_is_unknown_not_per_user(self):
        self.assertEqual(classify_identity("root"), UNKNOWN)

    def test_email_without_tld_is_unknown(self):
        self.assertEqual(classify_identity("someone@localhost"), UNKNOWN)


class ClassifyFailure(unittest.TestCase):
    """Ordering between these matters, because the service prefixes several of them identically.

    The workspace-membership, caller-permissions, scopes/audience and authorization-error strings were
    observed against a live gateway. The inbound-token, credential-provider, rate-limit, service-error and
    no-target strings come from AWS review of this sample against the service-side messages, not from a
    run of our own — the tests pin the mapping either way.
    """

    def test_caller_permissions_wins_over_generic_token_exchange_failed(self):
        # This string contains both "token exchange failed" and the specific permissions phrase.
        verdict, remedy = classify_failure("Token exchange failed: insufficient permissions for token exchange.")
        self.assertEqual(verdict, CALLER_PERMISSIONS)
        self.assertIn("CloudTrail", remedy)

    def test_scopes_audience_idp_is_exchange_refused_not_generic(self):
        verdict, remedy = classify_failure(
            "Token exchange failed: check credential provider scopes, audience, or IdP configuration."
        )
        self.assertEqual(verdict, EXCHANGE_REFUSED)
        self.assertIn("public-client", remedy)

    def test_bare_token_exchange_failed_falls_back_to_exchange_refused(self):
        verdict, _ = classify_failure("Token exchange failed.")
        self.assertEqual(verdict, EXCHANGE_REFUSED)

    def test_workspace_membership_is_detected(self):
        verdict, remedy = classify_failure(
            "invalid_client: user 'someone@example.com' is not a member of workspace 1234567890123456"
        )
        self.assertEqual(verdict, WORKSPACE_MEMBERSHIP)
        self.assertIn("workspace", remedy)

    def test_authorization_error_means_the_target_rejected_the_token(self):
        # The exchange succeeded and the target refused the delivered token, so the remedy is scopes and
        # object permissions. listingMode governs target sync only and must not appear here.
        verdict, remedy = classify_failure(
            "McpException - MCP listTools failed: Authorization error when sending message"
        )
        self.assertEqual(verdict, TARGET_REJECTED_TOKEN)
        self.assertIn("all-apis", remedy)
        self.assertIn("databricks-sql-access", remedy)
        self.assertNotIn("listingmode", remedy.lower())

    def test_a_composite_message_is_classified_by_its_cause_not_the_wrapper(self):
        # The client wraps the underlying failure, so the wrapper phrase must not win over a leaf cause.
        verdict, remedy = classify_failure(
            "McpException - MCP listTools failed: Authorization error when sending message: "
            "Token exchange failed: credential provider not found."
        )
        self.assertEqual(verdict, PROVIDER_NOT_FOUND)
        self.assertIn("providerArn", remedy)

    def test_no_target_found_is_its_own_verdict(self):
        verdict, remedy = classify_failure("No target found for capability: dbx-sql-te___execute_sql")
        self.assertEqual(verdict, TARGET_NOT_FOUND)
        self.assertIn("--target-name", remedy)

    def test_inbound_token_beats_the_catch_all(self):
        verdict, remedy = classify_failure(
            "Token exchange failed: inbound token is invalid or expired. Please re-authenticate."
        )
        self.assertEqual(verdict, INBOUND_TOKEN)
        self.assertNotIn("public-client", remedy)

    def test_credential_provider_not_found_beats_the_catch_all(self):
        verdict, remedy = classify_failure("Token exchange failed: credential provider not found.")
        self.assertEqual(verdict, PROVIDER_NOT_FOUND)
        self.assertIn("providerArn", remedy)
        self.assertNotIn("public-client", remedy)

    def test_rate_limited_beats_the_catch_all(self):
        verdict, remedy = classify_failure("Token exchange failed: rate limited. Please try again later.")
        self.assertEqual(verdict, TRANSIENT)
        self.assertNotIn("public-client", remedy)

    def test_service_error_is_transient_rather_than_unknown(self):
        verdict, _ = classify_failure("Token exchange encountered a service error. Please retry.")
        self.assertEqual(verdict, TRANSIENT)

    def test_transient_remedy_does_not_claim_which_stage_throttled(self):
        # "rate limited" is matched on message text alone and a warehouse can emit it too, so the remedy
        # sends the reader to the detail rather than asserting the exchange failed.
        _, remedy = classify_failure("Statement failed: the request was rate limited by the warehouse")
        self.assertIn("detail", remedy)
        self.assertNotIn("the exchange did not complete", remedy.lower())

    def test_specific_variants_never_inherit_the_enablement_remedy(self):
        # The complaint this guards: every "Token exchange failed:" message used to send the reader off to
        # request account enablement, including the three that have nothing to do with the allowlist.
        for text in (
            "Token exchange failed: inbound token is invalid or expired. Please re-authenticate.",
            "Token exchange failed: credential provider not found.",
            "Token exchange failed: rate limited. Please try again later.",
        ):
            with self.subTest(text=text):
                verdict, _ = classify_failure(text)
                self.assertNotEqual(verdict, EXCHANGE_REFUSED)

    def test_matching_is_case_insensitive(self):
        verdict, _ = classify_failure("TOKEN EXCHANGE FAILED: INSUFFICIENT PERMISSIONS FOR TOKEN EXCHANGE.")
        self.assertEqual(verdict, CALLER_PERMISSIONS)

    def test_empty_text_is_unknown(self):
        verdict, remedy = classify_failure("")
        self.assertEqual(verdict, UNKNOWN)
        self.assertIn("APPLICATION_LOGS", remedy)

    def test_unrecognised_text_is_unknown(self):
        verdict, _ = classify_failure("something nobody has seen before")
        self.assertEqual(verdict, UNKNOWN)


class QualifiedToolName(unittest.TestCase):
    def test_namespaces_the_tool(self):
        self.assertEqual(qualified_tool_name("dbx-sql-te", "execute_sql"), "dbx-sql-te___execute_sql")

    def test_already_qualified_name_is_left_alone(self):
        self.assertEqual(qualified_tool_name("dbx-sql-te", "other___execute_sql"), "other___execute_sql")

    def test_missing_target_raises(self):
        with self.assertRaises(ValueError):
            qualified_tool_name("", "execute_sql")

    def test_missing_tool_raises(self):
        with self.assertRaises(ValueError):
            qualified_tool_name("dbx-sql-te", "")


class ResponseParsing(unittest.TestCase):
    def test_extract_text_finds_the_text_block(self):
        response = {"result": {"content": [{"type": "text", "text": "hello"}]}}
        self.assertEqual(extract_text(response), "hello")

    def test_extract_text_skips_non_text_blocks(self):
        response = {"result": {"content": [{"type": "image"}, {"type": "text", "text": "hello"}]}}
        self.assertEqual(extract_text(response), "hello")

    def test_extract_text_returns_none_when_absent(self):
        self.assertIsNone(extract_text({"result": {}}))
        self.assertIsNone(extract_text({}))

    def test_is_error_detects_is_error_flag_on_http_200(self):
        self.assertTrue(is_error({"result": {"isError": True, "content": []}}))

    def test_is_error_detects_jsonrpc_error(self):
        self.assertTrue(is_error({"error": {"code": -32600, "message": "Unsupported protocol version"}}))

    def test_is_error_false_for_successful_result(self):
        self.assertFalse(is_error({"result": {"isError": False, "content": []}}))
        self.assertFalse(is_error({"result": {"content": []}}))


class ExtractIdentity(unittest.TestCase):
    def test_values_string_value_row_shape(self):
        text = (
            '{"statement_id":"01f1","status":{"state":"SUCCEEDED"},'
            '"result":{"data_array":[{"values":[{"string_value":"someone@example.com"}]}]}}'
        )
        self.assertEqual(extract_identity(text), "someone@example.com")

    def test_bare_list_row_shape(self):
        text = '{"result":{"data_array":[["someone@example.com"]]}}'
        self.assertEqual(extract_identity(text), "someone@example.com")

    def test_non_json_text_returns_none(self):
        self.assertIsNone(extract_identity("Token exchange failed."))

    def test_json_without_rows_returns_none(self):
        self.assertIsNone(extract_identity('{"result":{"data_array":[]}}'))

    def test_none_returns_none(self):
        self.assertIsNone(extract_identity(None))

    def test_json_array_payload_returns_none(self):
        self.assertIsNone(extract_identity("[1,2,3]"))


class ParseMcpBody(unittest.TestCase):
    """The endpoint may answer with plain JSON or with SSE frames, because we accept both."""

    PING = 'data: {"jsonrpc":"2.0","id":1,"result":{"ping":true}}'
    RESULT = 'data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"hi"}],"isError":false}}'

    def test_plain_json_body(self):
        self.assertEqual(parse_mcp_body('{"jsonrpc":"2.0","id":1,"result":{"ok":true}}')["id"], 1)

    def test_single_sse_frame(self):
        parsed = parse_mcp_body(f"event: message\n{self.RESULT}\n\n")
        self.assertEqual(extract_text(parsed), "hi")

    def test_multi_frame_sse_returns_the_frame_with_the_result(self):
        # A greedy scan over the whole body would splice both frames and fail to decode.
        body = f"event: message\n{self.PING}\n\nevent: message\n{self.RESULT}\n\n"
        parsed = parse_mcp_body(body)
        self.assertEqual(extract_text(parsed), "hi")
        self.assertEqual(parsed["id"], 2)

    def test_multi_frame_sse_with_crlf_separators(self):
        body = f"event: message\r\n{self.PING}\r\n\r\nevent: message\r\n{self.RESULT}\r\n\r\n"
        self.assertEqual(extract_text(parse_mcp_body(body)), "hi")

    def test_frame_carrying_an_error_is_preferred_over_a_bare_frame(self):
        bare = 'data: {"jsonrpc":"2.0","id":9}'
        err = 'data: {"jsonrpc":"2.0","id":10,"error":{"code":-32600,"message":"nope"}}'
        parsed = parse_mcp_body(f"{err}\n\n{bare}\n\n")
        self.assertTrue(is_error(parsed))

    def test_unparseable_frames_are_skipped(self):
        body = f"data: not-json\n\nevent: message\n{self.RESULT}\n\n"
        self.assertEqual(extract_text(parse_mcp_body(body)), "hi")

    def test_json_with_leading_noise_is_decoded(self):
        self.assertEqual(parse_mcp_body('noise {"jsonrpc":"2.0","id":3,"result":{}}')["id"], 3)

    def test_empty_body_raises(self):
        with self.assertRaises(ValueError):
            parse_mcp_body("   ")

    def test_body_without_json_raises(self):
        with self.assertRaises(ValueError):
            parse_mcp_body("503 Service Unavailable")

    def test_json_array_body_raises(self):
        with self.assertRaises(ValueError):
            parse_mcp_body("[1,2,3]")


class McpPostWiring(unittest.TestCase):
    """mcp_post must route the body through parse_mcp_body, not decode it itself."""

    SSE_TWO_FRAMES = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":1,"result":{"ping":true}}\n'
        "\n"
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"hi"}]}}\n'
        "\n"
    )

    def _urlopen_returning(self, body: str):
        response = mock.MagicMock()
        response.read.return_value = body.encode("utf-8")
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        return mock.Mock(return_value=response)

    SSE_LATE_UNRELATED = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"mine"}]}}\n'
        "\n"
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":99,"result":{"content":[{"type":"text","text":"not mine"}]}}\n'
        "\n"
    )

    def test_request_id_is_forwarded_to_the_parser(self):
        # Proves the wiring, not just the parser: mcp_post must hand its own request id to
        # parse_mcp_body, or a later unrelated result-bearing frame wins and the verdict is a
        # false UNKNOWN. The parser is covered separately in IdCorrelation.
        urlopen = self._urlopen_returning(self.SSE_LATE_UNRELATED)
        with mock.patch("check_obo_identity.urllib.request.urlopen", urlopen):
            result = mcp_post("https://example.invalid/mcp", "token", {"jsonrpc": "2.0", "id": 2}, None)
        self.assertEqual(extract_text(result), "mine")

    def test_multi_frame_sse_response_is_decoded(self):
        with mock.patch("check_obo_identity.urllib.request.urlopen", self._urlopen_returning(self.SSE_TWO_FRAMES)):
            result = mcp_post("https://example.invalid/mcp", "token", {"jsonrpc": "2.0", "id": 2}, None)
        self.assertEqual(extract_text(result), "hi")

    def test_protocol_version_header_is_sent_when_given(self):
        opener = self._urlopen_returning('{"jsonrpc":"2.0","id":1,"result":{}}')
        with mock.patch("check_obo_identity.urllib.request.urlopen", opener):
            mcp_post("https://example.invalid/mcp", "token", {"id": 1}, "2025-03-26")
        request = opener.call_args[0][0]
        self.assertEqual(request.get_header("Mcp-protocol-version"), "2025-03-26")
        self.assertEqual(request.get_header("Authorization"), "Bearer token")

    def test_protocol_version_header_is_omitted_when_none(self):
        opener = self._urlopen_returning('{"jsonrpc":"2.0","id":1,"result":{}}')
        with mock.patch("check_obo_identity.urllib.request.urlopen", opener):
            mcp_post("https://example.invalid/mcp", "token", {"id": 1}, None)
        self.assertIsNone(opener.call_args[0][0].get_header("Mcp-protocol-version"))


class ExitCodeMapping(unittest.TestCase):
    def test_every_verdict_has_an_exit_code(self):
        # Derived from the module rather than hand-listed: the hand-written list had already drifted,
        # omitting a newly added verdict while still claiming to cover every one.
        verdicts = {
            value
            for name, value in vars(check_obo_identity).items()
            if name.isupper() and isinstance(value, str) and value == name
        }
        self.assertTrue(verdicts)
        self.assertEqual(verdicts - set(EXIT_CODES), set())

    def test_only_per_user_exits_zero(self):
        zero = {verdict for verdict, code in EXIT_CODES.items() if code == 0}
        self.assertEqual(zero, {PER_USER})


class IdCorrelation(unittest.TestCase):
    """A later result-bearing frame must not displace the reply to the request we actually sent."""

    LATE_UNRELATED_RESULT = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"mine"}]}}\n'
        "\n"
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":99,"result":{"content":[{"type":"text","text":"not mine"}]}}\n'
        "\n"
    )

    def test_expected_id_wins_over_a_later_frame(self):
        message = parse_mcp_body(self.LATE_UNRELATED_RESULT, expected_id=2)
        self.assertEqual(extract_text(message), "mine")

    def test_without_expected_id_the_last_frame_still_wins(self):
        message = parse_mcp_body(self.LATE_UNRELATED_RESULT)
        self.assertEqual(extract_text(message), "not mine")

    def test_unmatched_id_falls_back_rather_than_raising(self):
        message = parse_mcp_body(self.LATE_UNRELATED_RESULT, expected_id=7)
        self.assertEqual(extract_text(message), "not mine")


class TransportFailure(unittest.TestCase):
    """A gateway we cannot reach is a transport problem, reported as itself rather than as UNKNOWN."""

    def _raising(self, exc):
        return mock.Mock(side_effect=exc)

    def test_url_error_raises_gateway_unreachable(self):
        import urllib.error

        opener = self._raising(urllib.error.URLError("no host"))
        with mock.patch("check_obo_identity.urllib.request.urlopen", opener), self.assertRaises(GatewayUnreachable):
            mcp_post("https://example.invalid/mcp", "t", {"jsonrpc": "2.0", "id": 1}, None)

    def test_socket_timeout_raises_gateway_unreachable(self):
        opener = self._raising(TimeoutError("timed out"))
        with mock.patch("check_obo_identity.urllib.request.urlopen", opener), self.assertRaises(GatewayUnreachable):
            mcp_post("https://example.invalid/mcp", "t", {"jsonrpc": "2.0", "id": 1}, None)

    def test_http_error_is_not_a_transport_failure(self):
        import io
        import urllib.error

        body = b'{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"x"}]}}'
        err = urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(body))
        with mock.patch("check_obo_identity.urllib.request.urlopen", self._raising(err)):
            result = mcp_post("https://example.invalid/mcp", "t", {"jsonrpc": "2.0", "id": 1}, None)
        self.assertEqual(extract_text(result), "x")

    def test_main_reports_the_verdict_and_exits_four(self):
        # Assert the verdict, not just the exit status: UNKNOWN also exits 4, so an exit-code-only
        # assertion passes even when main stops recognising this exception.
        argv = ["--gateway-url", "https://example.invalid/mcp", "--token", "t", "--target-name", "tgt", "--json"]
        buffer = io.StringIO()
        patched = mock.patch("check_obo_identity.run_check", side_effect=GatewayUnreachable("boom"))
        with patched, contextlib.redirect_stdout(buffer):
            code = main(argv)
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(buffer.getvalue())["verdict"], GATEWAY_UNREACHABLE)

    def test_verdict_has_an_exit_code(self):
        self.assertEqual(EXIT_CODES[GATEWAY_UNREACHABLE], 4)


class IdTypeTolerance(unittest.TestCase):
    """A gateway may echo the JSON-RPC id as a string; correlation must not silently fall back."""

    STRING_ID = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":"2","result":{"content":[{"type":"text","text":"mine"}]}}\n'
        "\n"
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":99,"result":{"content":[{"type":"text","text":"not mine"}]}}\n'
        "\n"
    )

    def test_string_id_still_matches_an_int_request_id(self):
        self.assertEqual(extract_text(parse_mcp_body(self.STRING_ID, expected_id=2)), "mine")


class QueryTimeoutEnv(unittest.TestCase):
    """A bad OBO_QUERY_TIMEOUT must not crash at import, before the tool can report anything."""

    def _read(self, value):
        with mock.patch.dict(os.environ, {"OBO_QUERY_TIMEOUT": value}), contextlib.redirect_stderr(io.StringIO()):
            return check_obo_identity._query_timeout()

    def test_unset_uses_the_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(check_obo_identity._query_timeout(), 180)

    def test_valid_value_is_honoured(self):
        self.assertEqual(self._read("42"), 42)

    def test_non_numeric_falls_back_instead_of_raising(self):
        self.assertEqual(self._read("soon"), 180)

    def test_non_positive_falls_back(self):
        self.assertEqual(self._read("0"), 180)
        self.assertEqual(self._read("-5"), 180)


class TextBlockSelection(unittest.TestCase):
    """A leading text block with no text must not mask a later, valid one."""

    def test_null_text_block_is_skipped(self):
        response = {"result": {"content": [{"type": "text"}, {"type": "text", "text": "real"}]}}
        self.assertEqual(extract_text(response), "real")

    def test_all_blocks_empty_returns_none(self):
        self.assertIsNone(extract_text({"result": {"content": [{"type": "text"}]}}))


class SubjectFromToken(unittest.TestCase):
    def test_reads_the_default_email_claim(self):
        self.assertEqual(subject_from_token(_jwt({"email": "user@example.com"})), "user@example.com")

    def test_reads_a_named_claim(self):
        token = _jwt({"email": "shared@example.com", "sub": "user@example.com"})
        self.assertEqual(subject_from_token(token, "sub"), "user@example.com")

    def test_segment_needing_padding_still_decodes(self):
        # base64url of a JWT payload is stripped of "=" padding, so every length remainder must decode.
        for filler in ("a", "ab", "abc", "abcd"):
            with self.subTest(filler=filler):
                token = _jwt({"email": f"{filler}@example.com"})
                self.assertEqual(subject_from_token(token), f"{filler}@example.com")

    def test_missing_claim_is_none(self):
        self.assertIsNone(subject_from_token(_jwt({"sub": "user@example.com"})))

    def test_non_string_claim_is_none(self):
        self.assertIsNone(subject_from_token(_jwt({"email": {"nested": "user@example.com"}})))

    def test_blank_claim_is_none(self):
        self.assertIsNone(subject_from_token(_jwt({"email": "   "})))

    def test_not_a_jwt_is_none(self):
        self.assertIsNone(subject_from_token("opaque-access-token"))

    def test_undecodable_payload_is_none(self):
        self.assertIsNone(subject_from_token("header.!!!not-base64!!!.signature"))

    def test_json_array_payload_is_none(self):
        segment = base64.urlsafe_b64encode(json.dumps(["user@example.com"]).encode()).decode().rstrip("=")
        self.assertIsNone(subject_from_token(f"header.{segment}.signature"))

    def test_empty_inputs_are_none(self):
        self.assertIsNone(subject_from_token(None))
        self.assertIsNone(subject_from_token(""))
        self.assertIsNone(subject_from_token(_jwt({"email": "user@example.com"}), ""))


class ResolveIdentity(unittest.TestCase):
    """The shape of the principal and the presented claim are combined by one pure function."""

    def test_exact_match_outranks_shape(self):
        self.assertEqual(resolve_identity(UNKNOWN, "alice", "alice")[0], PER_USER)

    def test_service_principal_short_circuits(self):
        self.assertEqual(
            resolve_identity(SERVICE_PRINCIPAL, "3f2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d", None)[0], SERVICE_PRINCIPAL
        )

    def test_two_humans_is_a_mismatch(self):
        self.assertEqual(resolve_identity(PER_USER, "svc@example.com", "user@example.com")[0], IDENTITY_MISMATCH)

    def test_opaque_subject_is_unverified(self):
        self.assertEqual(resolve_identity(PER_USER, "user@example.com", "a-guid")[0], PER_USER_UNVERIFIED)

    def test_missing_subject_is_unverified(self):
        self.assertEqual(resolve_identity(PER_USER, "user@example.com", None)[0], PER_USER_UNVERIFIED)

    def test_unclassifiable_without_a_match_stays_unknown(self):
        self.assertEqual(resolve_identity(UNKNOWN, "alice", "bob")[0], UNKNOWN)


def _identity_responses(principal: str) -> list:
    """An initialize reply followed by a tools/call reply carrying current_user()."""
    payload = json.dumps({"result": {"data_array": [[principal]]}})
    return [
        {"result": {"protocolVersion": "2025-06-18"}},
        {"result": {"content": [{"type": "text", "text": payload}]}},
    ]


class SubjectComparison(unittest.TestCase):
    """An email-shaped principal alone is not proof: a shared account can have an email for a username."""

    def _run(self, principal: str, token: str, **kwargs):
        with mock.patch("check_obo_identity.mcp_post", side_effect=_identity_responses(principal)):
            return run_check("https://gw.invalid/mcp", token, "tgt", "execute_sql", "SELECT 1", "2025-06-18", **kwargs)

    def test_matching_subject_is_per_user(self):
        result = self._run("user@example.com", _jwt({"email": "user@example.com"}))
        self.assertEqual(result["verdict"], PER_USER)
        self.assertEqual(result["remedy"], REMEDY_PER_USER)
        self.assertEqual(result["subject"], "user@example.com")

    def test_comparison_ignores_case(self):
        result = self._run("user@example.com", _jwt({"email": "User@Example.COM"}))
        self.assertEqual(result["verdict"], PER_USER)

    def test_shared_account_is_a_mismatch_not_per_user(self):
        result = self._run("svc-analytics@example.com", _jwt({"email": "user@example.com"}))
        self.assertEqual(result["verdict"], IDENTITY_MISMATCH)
        self.assertEqual(result["identity"], "svc-analytics@example.com")
        self.assertEqual(result["subject"], "user@example.com")
        self.assertIn("shared account", result["remedy"])

    def test_unreadable_token_is_unverified_rather_than_a_pass(self):
        # Exit 0 on a comparison that never happened is the same green as a verified match, which is the
        # hole the comparison exists to close.
        result = self._run("user@example.com", "opaque-access-token")
        self.assertEqual(result["verdict"], PER_USER_UNVERIFIED)
        self.assertEqual(result["remedy"], REMEDY_PER_USER_UNCHECKED)
        self.assertIsNone(result["subject"])
        self.assertNotEqual(EXIT_CODES[PER_USER_UNVERIFIED], 0)

    def test_cognito_access_token_has_no_email_claim_and_does_not_pass(self):
        token = _jwt({"sub": "9f1c2d3e-4a5b-6c7d-8e9f-0a1b2c3d4e5f", "token_use": "access", "client_id": "abc"})
        result = self._run("user@example.com", token)
        self.assertEqual(result["verdict"], PER_USER_UNVERIFIED)

    def test_opaque_subject_is_unverified_not_a_mismatch(self):
        # sub is a GUID on Entra and Cognito. It cannot be a Databricks username, so disagreeing with an
        # email-shaped principal proves nothing and must not be reported as delegation failing.
        token = _jwt({"sub": "9f1c2d3e-4a5b-6c7d-8e9f-0a1b2c3d4e5f"})
        result = self._run("user@example.com", token, subject_claim="sub")
        self.assertEqual(result["verdict"], PER_USER_UNVERIFIED)
        self.assertEqual(result["remedy"], REMEDY_SUBJECT_NOT_COMPARABLE)
        self.assertIn("preferred_username", result["remedy"])

    def test_non_email_principal_matching_the_claim_is_per_user(self):
        # A workspace whose usernames are not email-shaped: the exact match is the evidence, not the shape.
        result = self._run("alice", _jwt({"preferred_username": "alice"}), subject_claim="preferred_username")
        self.assertEqual(result["verdict"], PER_USER)
        self.assertEqual(result["remedy"], REMEDY_PER_USER)

    def test_service_principal_verdict_is_unchanged(self):
        result = self._run("3f2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d", _jwt({"email": "user@example.com"}))
        self.assertEqual(result["verdict"], SERVICE_PRINCIPAL)

    def test_named_claim_is_used_for_the_comparison(self):
        token = _jwt({"sub": "user@example.com", "email": "shared@example.com"})
        result = self._run("user@example.com", token, subject_claim="sub")
        self.assertEqual(result["verdict"], PER_USER)


class SubjectClaimCli(unittest.TestCase):
    """--subject-claim has to reach run_check, not just exist on the parser."""

    TOKEN = _jwt({"sub": "user@example.com", "email": "shared@example.com"})

    def _main(self, extra: list) -> tuple:
        argv = [
            "--gateway-url",
            "https://gw.invalid/mcp",
            "--target-name",
            "tgt",
            "--token",
            self.TOKEN,
            "--json",
            *extra,
        ]
        buffer = io.StringIO()
        patched = mock.patch("check_obo_identity.mcp_post", side_effect=_identity_responses("user@example.com"))
        with patched, contextlib.redirect_stdout(buffer):
            code = main(argv)
        return code, json.loads(buffer.getvalue())

    def test_default_claim_compares_email_and_reports_the_mismatch(self):
        code, result = self._main([])
        self.assertEqual(result["verdict"], IDENTITY_MISMATCH)
        self.assertEqual(code, EXIT_CODES[IDENTITY_MISMATCH])

    def test_named_claim_is_forwarded_and_clears_the_mismatch(self):
        code, result = self._main(["--subject-claim", "sub"])
        self.assertEqual(result["verdict"], PER_USER)
        self.assertEqual(code, 0)

    def test_subject_is_printed_in_the_human_report(self):
        argv = [
            "--gateway-url",
            "https://gw.invalid/mcp",
            "--target-name",
            "tgt",
            "--token",
            self.TOKEN,
            "--subject-claim",
            "sub",
        ]
        buffer = io.StringIO()
        patched = mock.patch("check_obo_identity.mcp_post", side_effect=_identity_responses("user@example.com"))
        with patched, contextlib.redirect_stdout(buffer):
            main(argv)
        self.assertIn("subject : user@example.com", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
