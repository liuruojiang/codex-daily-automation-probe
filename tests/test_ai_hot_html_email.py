from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import mail_utils  # noqa: E402


class FakeSmtp:
    sent_messages: list[object] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> "FakeSmtp":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def login(self, *_args: object) -> None:
        pass

    def send_message(self, message: object) -> None:
        self.sent_messages.append(message)


class AiHotHtmlEmailTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSmtp.sent_messages.clear()

    def test_html_alternative_is_inline_and_has_no_attachment(self) -> None:
        env = {
            "MAIL_SERVER": "smtp.example.com",
            "MAIL_PORT": "465",
            "MAIL_USERNAME": "user",
            "MAIL_PASSWORD": "password",
            "MAIL_FROM": "sender@example.com",
            "MAIL_TO": "reader@example.com",
            "MAIL_USE_SSL": "true",
        }
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
            mail_utils.smtplib, "SMTP_SSL", FakeSmtp
        ):
            mail_utils.send_mail(
                "AI HOT 日报 - 2026-08-22",
                "完整纯文本正文",
                attachment=None,
                html_body="<html><body><h1>完整 HTML 正文</h1></body></html>",
            )

        self.assertEqual(len(FakeSmtp.sent_messages), 1)
        message = FakeSmtp.sent_messages[0]
        self.assertEqual(message.get_content_type(), "multipart/alternative")
        self.assertEqual([part.get_content_type() for part in message.iter_parts()], ["text/plain", "text/html"])
        self.assertEqual(list(message.iter_attachments()), [])


if __name__ == "__main__":
    unittest.main()
