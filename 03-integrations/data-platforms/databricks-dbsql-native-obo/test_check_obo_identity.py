"""Tests for check_obo_identity.

Run with either:
    python -m unittest discover -v
    pytest -v
"""

import unittest
from unittest import mock

from check_obo_identity import (
    CALLER_PERMISSIONS,
    EXCHANGE_REFUSED,
    EXIT_CODES,
    PER_USER,
    SERVICE_PRINCIPAL,
    TARGET_UNREACHABLE,
    UNKNOWN,
    WORKSPACE_MEMBERSHIP,
    classify_failure,
    classify_identity,
    extract_identity,
    extract_text,
    is_error,
    mcp_post,
    parse_mcp_body,
    qualified_tool_name,
)


class ClassifyIdentity(unittest.TestCase):
    def test_email_is_per_user(self):
        self.assertEqual(classify_identity("someone@example.com"), PER_USER)

    def test_application_uuid_is_service_principal(self):
        self.assertEqual(classify_identity("00000000-0000-4000-8000-000000000000"), SERVICE_PRINCIPAL)

    def test_uppercase_uuid_is_service_principal(self):
        self.assertEqual(classify_identity("00000000-0000-4000-8000-000000000000"), SERVICE_PRINCIPAL)

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
    """The verbatim strings below were observed from AgentCore Gateway; ordering between them matters."""

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

    def test_target_sync_failure_is_detected(self):
        verdict, remedy = classify_failure(
            "McpException - MCP listTools failed: Authorization error when sending message"
        )
        self.assertEqual(verdict, TARGET_UNREACHABLE)
        self.assertIn("DYNAMIC", remedy)

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
        verdicts = {
            PER_USER,
            SERVICE_PRINCIPAL,
            EXCHANGE_REFUSED,
            CALLER_PERMISSIONS,
            TARGET_UNREACHABLE,
            WORKSPACE_MEMBERSHIP,
            UNKNOWN,
        }
        self.assertEqual(verdicts - set(EXIT_CODES), set())

    def test_only_per_user_exits_zero(self):
        zero = {verdict for verdict, code in EXIT_CODES.items() if code == 0}
        self.assertEqual(zero, {PER_USER})


if __name__ == "__main__":
    unittest.main()
