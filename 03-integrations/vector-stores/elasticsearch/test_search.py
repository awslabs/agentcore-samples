import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from handler import lambda_handler, lookup


class PolicySearch(unittest.TestCase):
    def test_request_and_result(self):
        connection = Mock()
        hit = {
            "_source": {"filename": "warranty.txt", "attachment": {"content": "a" * 1000}, "private": "not exported"}
        }
        connection.getresponse.return_value = Mock(
            status=200, read=Mock(return_value=json.dumps({"hits": {"hits": [hit]}}).encode())
        )
        factory = Mock(return_value=connection)
        result = lookup("warranty", "https://search.invalid", "sandbox", lambda: "fixture", factory)
        factory.assert_called_once_with("search.invalid", port=443, timeout=8)
        call = connection.request.call_args
        self.assertEqual(call.args, ("POST", "/sandbox/_search"))
        self.assertEqual(call.kwargs["headers"]["Authorization"], "ApiKey fixture")
        self.assertEqual(
            json.loads(call.kwargs["body"]),
            {
                "size": 3,
                "_source": ["filename", "attachment.content"],
                "query": {"multi_match": {"query": "warranty", "fields": ["attachment.content"]}},
            },
        )
        self.assertEqual(result, {"matches": [{"filename": "warranty.txt", "excerpt": "a" * 600}]})
        connection.close.assert_called_once()

    def test_validate_before_reading_secret(self):
        key = Mock(side_effect=AssertionError("unnecessary lookup"))
        for origin, index, query in [
            ("http://search.invalid", "sandbox", "a"),
            ("https://user@search.invalid", "sandbox", "a"),
            ("https://search.invalid/other", "sandbox", "a"),
            ("https://search.invalid", "*", "a"),
            ("https://search.invalid", "sandbox", ""),
        ]:
            with self.assertRaises(ValueError):
                lookup(query, origin, index, key)
        key.assert_not_called()

    def test_redirect_and_oversize(self):
        for status, raw, error in [(302, b"", RuntimeError), (200, b"x" * (512 * 1024 + 1), ValueError)]:
            conn = Mock(getresponse=Mock(return_value=Mock(status=status, read=Mock(return_value=raw))))
            with self.assertRaises(error):
                lookup("item", "https://search.invalid", "sandbox", lambda: "fixture", Mock(return_value=conn))
            self.assertEqual(conn.request.call_count, 1)
            conn.close.assert_called_once()

    def test_wrong_dispatch(self):
        context = SimpleNamespace(client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": "other___search"}))
        with self.assertRaises(ValueError):
            lambda_handler({"query": "q"}, context)
