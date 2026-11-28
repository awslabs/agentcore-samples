import threading
import unittest
from http.client import HTTPConnection
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from scripts import chat


class CognitoPKCE(unittest.TestCase):
    def test_loopback_and_code_exchange(self):
        statuses = []
        threads = []
        seen = {}

        def browser(url):
            query = parse_qs(urlsplit(url).query)
            seen.update(query)

            def callbacks():
                for state in ["wrong", query["state"][0]]:
                    conn = HTTPConnection("localhost", 3000, timeout=3)
                    try:
                        conn.request("GET", "/callback?" + urlencode({"state": state, "code": "fixture-code"}))
                        res = conn.getresponse()
                        statuses.append(res.status)
                        res.read()
                    finally:
                        conn.close()

            thread = threading.Thread(target=callbacks)
            threads.append(thread)
            thread.start()
            return True

        conn = Mock()
        conn.getresponse.return_value = Mock(status=200, read=lambda n: b'{"access_token":"fixture-access"}')
        with (
            patch.object(chat.webbrowser, "open", side_effect=browser),
            patch.object(chat, "HTTPSConnection", return_value=conn),
        ):
            self.assertEqual(chat.login("fixture.invalid", "fixture-client"), "fixture-access")
        for thread in threads:
            thread.join(3)
        self.assertEqual(statuses, [400, 200])
        self.assertEqual(seen["code_challenge_method"], ["S256"])
        body = parse_qs(
            conn.request.call_args.args[2]
            if len(conn.request.call_args.args) > 2
            else conn.request.call_args.kwargs["body"]
        )
        verifier = body["code_verifier"][0]
        self.assertEqual(
            chat.base64.urlsafe_b64encode(chat.hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="),
            seen["code_challenge"][0],
        )
        conn.close.assert_called_once()
