import unittest

from telegram_api import encode_multipart
from telegram_apk import render_caption
from telegram_notify import RULE


class CaptionTest(unittest.TestCase):
    def test_caption_layout(self):
        text = render_caption(
            "ovrly", "0.1.0", "a1b2c3d4e5f6", "feat(android): share intake for videos",
            "natnael-solomon", "https://github.com/o/r", "unsigned release build",
        )
        lines = text.split("\n")
        self.assertEqual(lines[0], '<b><a href="https://github.com/o/r/commit/a1b2c3d4e5f6">ovrly 0.1.0 (a1b2c3d)</a></b>')
        self.assertEqual(lines[1], RULE)
        self.assertEqual(lines[2], "<blockquote>feat(android): shar…</blockquote>")
        self.assertEqual(lines[3], "")
        self.assertEqual(lines[4], "<b>Type</b>  unsigned release build")
        self.assertEqual(lines[5], "<b>By</b>  <i>sol</i>")
        self.assertEqual(len(lines), 6)

    def test_caption_without_subject(self):
        text = render_caption("ovrly", "0.1.0", "abc1234", "", "dev", "u", "t")
        self.assertNotIn("blockquote", text)


class MultipartTest(unittest.TestCase):
    def test_encodes_fields_and_file(self):
        body, content_type = encode_multipart({"chat_id": "1", "caption": "<b>x</b>"}, "document", "app.apk", b"\x00\x01")
        boundary = content_type.split("boundary=")[1]
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertIn(f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n1\r\n'.encode(), body)
        self.assertIn(b'name="caption"\r\n\r\n<b>x</b>\r\n', body)
        self.assertIn(b'name="document"; filename="app.apk"\r\nContent-Type: application/octet-stream\r\n\r\n\x00\x01\r\n', body)
        self.assertTrue(body.endswith(f"--{boundary}--\r\n".encode()))


if __name__ == "__main__":
    unittest.main()
