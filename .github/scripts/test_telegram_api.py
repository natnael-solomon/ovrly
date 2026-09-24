import unittest

from telegram_api import TelegramClient, TelegramError, VariableState, escape, http_json, truncate


class FakeTelegram(TelegramClient):
    """Records API calls and allocates message IDs; used by the notifier tests too."""

    def __init__(self, missing=()):
        super().__init__("token", "-100", transport=None)
        self.calls = []
        self.next_id = 100
        self.missing = set(missing)

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == "sendMessage":
            self.next_id += 1
            return {"message_id": self.next_id}
        if method == "editMessageText" and params["message_id"] in self.missing:
            return None
        return {}

    def sent(self):
        return [p["text"] for m, p in self.calls if m == "sendMessage"]

    def methods(self):
        return [m for m, _ in self.calls]


class EscapingTest(unittest.TestCase):
    def test_escapes_markup_but_not_quotes(self):
        self.assertEqual(escape('<a & "b">'), '&lt;a &amp; "b"&gt;')

    def test_display_names(self):
        from telegram_api import display_name
        self.assertEqual(display_name("natnael-solomon"), "sol")
        self.assertEqual(display_name("Nattyy-1"), "sancho")
        self.assertEqual(display_name("Neb-iyu"), "neba")
        self.assertEqual(display_name("some<one>"), "some&lt;one&gt;")

    def test_truncate_collapses_whitespace(self):
        self.assertEqual(truncate("a   b\n\nc", 10), "a b c")
        self.assertEqual(truncate("abcdefghij", 5), "abcd…")


class ClientTest(unittest.TestCase):
    def transport(self, status, response):
        def fake(url, method="GET", payload=None, headers=None):
            self.request = (url, method, payload)
            return status, response
        return fake

    def test_send_uses_html_and_silences(self):
        client = TelegramClient("t", "-1", self.transport(200, {"ok": True, "result": {"message_id": 7}}))
        self.assertEqual(client.send("<b>x</b>"), 7)
        url, method, payload = self.request
        self.assertTrue(url.endswith("/bott/sendMessage"))
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertTrue(payload["disable_notification"])
        self.assertTrue(payload["link_preview_options"]["is_disabled"])

    def test_benign_errors_are_ignored(self):
        client = TelegramClient("t", "-1", self.transport(400, {"ok": False, "description": "Bad Request: message is not modified"}))
        self.assertIsNone(client.call("editMessageText"))
        missing = TelegramClient("t", "-1", self.transport(400, {"ok": False, "description": "Bad Request: message to edit not found"}))
        self.assertFalse(missing.edit(1, "x"))

    def test_other_errors_raise_without_token(self):
        client = TelegramClient("secret", "-1", self.transport(401, {"ok": False, "description": "Unauthorized"}))
        with self.assertRaises(TelegramError) as raised:
            client.send("x")
        self.assertNotIn("secret", str(raised.exception))


class HttpJsonTest(unittest.TestCase):
    def test_returns_real_status_and_empty_body(self):
        import io
        from unittest.mock import patch

        class Response(io.BytesIO):
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with patch("telegram_api.urllib.request.urlopen", return_value=Response(b"")):
            self.assertEqual(http_json("https://example.invalid", "PATCH", {}), (204, {}))


class VariableStateTest(unittest.TestCase):
    def test_missing_variable_is_created_on_save(self):
        requests = []

        def fake(url, method="GET", payload=None, headers=None):
            requests.append((url, method, payload))
            if method == "GET":
                return 404, {}
            return 201, {}

        store = VariableState("o/r", "gh", "STATE", fake)
        self.assertEqual(store.load(), {})
        store.save({"a": 1})
        self.assertEqual(requests[1][1], "POST")
        self.assertEqual(requests[1][2], {"name": "STATE", "value": '{"a":1}'})

    def test_existing_variable_is_patched(self):
        requests = []

        def fake(url, method="GET", payload=None, headers=None):
            requests.append((url, method))
            if method == "GET":
                return 200, {"value": '{"a": 1}'}
            return 204, {}

        store = VariableState("o/r", "gh", "STATE", fake)
        self.assertEqual(store.load(), {"a": 1})
        store.save({"a": 2})
        self.assertEqual(requests[1], ("https://api.github.com/repos/o/r/actions/variables/STATE", "PATCH"))

    def test_oversized_state_is_rejected(self):
        store = VariableState("o/r", "gh", "STATE", lambda *a, **k: (204, {}))
        with self.assertRaises(RuntimeError):
            store.save({"blob": "x" * 50_000})


if __name__ == "__main__":
    unittest.main()
