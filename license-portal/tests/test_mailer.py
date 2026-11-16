from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PORTAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORTAL))

from license_portal.config import Config
from license_portal.email_templates import RenderedEmail, render_code_email, render_notice_email
from license_portal.mailer import SMTPMailer, build_mailer


class CapturingMailer(SMTPMailer):
    def __init__(self, **kwargs) -> None:
        defaults = {
            "host": "smtp.example.com",
            "port": 587,
            "sender": "portal@example.com",
            "sender_name": "PyShield 授权中心",
            "reply_to": "support@example.com",
            "site_name": "PyShield 授权中心",
            "site_url": "https://license.example.com",
            "support_email": "support@example.com",
            "contact_wechat": "pyshield-support",
        }
        defaults.update(kwargs)
        super().__init__(**defaults)
        self.message = None

    def _send(self, message) -> None:
        self.message = message


class ConfigMailTests(unittest.TestCase):
    def test_sender_defaults_follow_site_and_support_settings(self) -> None:
        config = Config(
            site_name="Acme 授权中心",
            support_email="help@acme.example",
            smtp_host="smtp.example.com",
            smtp_user="bot@example.com",
        )
        self.assertEqual(config.smtp_from, "bot@example.com")
        self.assertEqual(config.smtp_from_name, "Acme 授权中心")
        self.assertEqual(config.smtp_reply_to, "help@acme.example")

    def test_env_overrides_sender_name_and_reply_to(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps({
                "data_dir": temporary,
                "smtp_host": "smtp.example.com",
                "smtp_user": "bot@example.com",
                "smtp_password": "secret",
                "smtp_from": "bot@example.com",
                "smtp_from_name": "JSON 发件人",
                "smtp_reply_to": "json-reply@example.com",
            }), encoding="utf-8")
            with mock.patch.dict(os.environ, {
                "PYSHELD_PORTAL_SMTP_FROM_NAME": "环境发件人",
                "PYSHELD_PORTAL_SMTP_REPLY_TO": "env-reply@example.com",
            }, clear=False):
                config = Config.load(path)
        self.assertEqual(config.smtp_from_name, "环境发件人")
        self.assertEqual(config.smtp_reply_to, "env-reply@example.com")


class EmailTemplateTests(unittest.TestCase):
    def test_code_email_uses_brand_shell_and_plain_text_fallback(self) -> None:
        rendered = render_code_email(
            code="123456",
            site_name="Acme 授权中心",
            site_url="https://license.example.com",
            support_email="help@example.com",
            contact_wechat="wx-help",
        )
        self.assertIsInstance(rendered, RenderedEmail)
        self.assertEqual(rendered.subject, "【Acme 授权中心】邮箱验证码")
        self.assertIn("123456", rendered.text)
        self.assertIn("10 分钟", rendered.text)
        self.assertIn("help@example.com", rendered.text)
        self.assertIn("wx-help", rendered.text)
        self.assertIn("https://license.example.com", rendered.text)
        self.assertIn("<!DOCTYPE html>", rendered.html)
        self.assertIn("EMAIL VERIFICATION", rendered.html)
        self.assertIn("123456", rendered.html)
        self.assertIn("打开授权中心", rendered.html)
        self.assertIn("LICENSE CENTER", rendered.html)
        self.assertIn("copy-code.html?minutes=10", rendered.html)
        self.assertIn("support=help%40example.com", rendered.html)
        self.assertIn("wechat=wx-help", rendered.html)
        self.assertIn("#123456", rendered.html)
        self.assertIn("复制验证码：", rendered.text)
        self.assertNotIn("<script", rendered.html.lower())
        self.assertNotIn("<link", rendered.html.lower())
        self.assertNotIn("<img", rendered.html.lower())

    def test_template_escapes_site_name_and_notice_body(self) -> None:
        rendered = render_code_email(
            code="654321",
            site_name='<script>alert("x")</script>',
            support_email='help@example.com"><script>alert(1)</script>',
        )
        self.assertNotIn("<script", rendered.html.lower())
        self.assertIn("&lt;script&gt;", rendered.html)

        notice = render_notice_email(
            subject="订单状态更新",
            text="第一段\n\n<script>alert(1)</script>",
            site_name="Acme 授权中心",
            site_url="https://license.example.com",
        )
        self.assertEqual(notice.subject, "【Acme 授权中心】订单状态更新")
        self.assertNotIn("<script", notice.html.lower())
        self.assertIn("&lt;script&gt;", notice.html)
        self.assertIn("第一段", notice.html)

    def test_invalid_site_url_is_not_linked(self) -> None:
        rendered = render_code_email(
            code="111111",
            site_name="Acme",
            site_url="javascript:alert(1)",
        )
        self.assertNotIn("javascript:", rendered.html)
        self.assertNotIn("打开授权中心", rendered.html)
        self.assertNotIn("copy-code.html", rendered.html)


class SMTPMessageTests(unittest.TestCase):
    def test_code_message_has_branded_html_reply_to_and_plain_text(self) -> None:
        mailer = CapturingMailer()
        mailer.send_code("user@example.com", "246810")
        message = mailer.message
        self.assertIsNotNone(message)
        self.assertIn("portal@example.com", str(message["From"]))
        self.assertEqual(str(message["Reply-To"]), "support@example.com")
        self.assertEqual(str(message["Auto-Submitted"]), "auto-generated")
        self.assertIn("邮箱验证码", str(message["Subject"]))

        parts = list(message.iter_parts())
        plain = next(part for part in parts if part.get_content_type() == "text/plain")
        html = next(part for part in parts if part.get_content_type() == "text/html")
        self.assertIn("246810", plain.get_content())
        self.assertIn("246810", html.get_content())
        self.assertIn("LICENSE CENTER", html.get_content())

    def test_notice_message_is_branded(self) -> None:
        mailer = CapturingMailer()
        mailer.send_notice("user@example.com", "订单已收款", "订单 #1 已完成收款。")
        message = mailer.message
        self.assertIn("订单已收款", str(message["Subject"]))
        parts = list(message.iter_parts())
        html = next(part for part in parts if part.get_content_type() == "text/html")
        self.assertIn("SYSTEM NOTICE", html.get_content())
        self.assertIn("订单 #1", html.get_content())

    def test_build_mailer_passes_branding_context(self) -> None:
        config = Config(
            site_name="Acme 授权中心",
            site_url="https://license.example.com",
            support_email="help@example.com",
            contact_wechat="wx-help",
            smtp_host="smtp.example.com",
            smtp_user="bot@example.com",
            smtp_password="secret",
            smtp_from_name="Acme Support",
            smtp_reply_to="reply@example.com",
        )
        mailer = build_mailer(config)
        self.assertIsInstance(mailer, SMTPMailer)
        self.assertEqual(mailer.sender_name, "Acme Support")
        self.assertEqual(mailer.reply_to, "reply@example.com")
        self.assertEqual(mailer.site_name, "Acme 授权中心")
        self.assertEqual(mailer.site_url, "https://license.example.com")


if __name__ == "__main__":
    unittest.main()
