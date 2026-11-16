"""E-mail delivery for verification codes and operational notices."""

from __future__ import annotations

import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formataddr

from .config import Config
from .email_templates import render_code_email, render_notice_email


class MailerError(Exception):
    """Raised when an e-mail cannot be delivered."""


class Mailer:
    def send_code(self, email: str, code: str, purpose: str = "trial") -> None:
        raise NotImplementedError

    def send_notice(self, email: str, subject: str, text: str) -> None:
        raise NotImplementedError


class ConsoleMailer(Mailer):
    """Development mailer: prints the code instead of sending it."""

    def send_code(self, email: str, code: str, purpose: str = "trial") -> None:
        print(
            f"[license-portal] verification code for {email} ({purpose}): {code}",
            file=sys.stderr,
            flush=True,
        )

    def send_notice(self, email: str, subject: str, text: str) -> None:
        print(f"[license-portal] mail to {email}: {subject}\n{text}", file=sys.stderr, flush=True)


class MemoryMailer(Mailer):
    """Test double that records the last code and messages."""

    def __init__(self) -> None:
        self.codes: list[tuple[str, str, str]] = []
        self.messages: list[tuple[str, str, str]] = []

    @property
    def last_code(self) -> str:
        return self.codes[-1][1] if self.codes else ""

    def send_code(self, email: str, code: str, purpose: str = "trial") -> None:
        self.codes.append((email, code, purpose))

    def send_notice(self, email: str, subject: str, text: str) -> None:
        self.messages.append((email, subject, text))


class SMTPMailer(Mailer):
    def __init__(
        self,
        host: str,
        port: int,
        username: str = "",
        password: str = "",
        sender: str = "",
        sender_name: str = "",
        reply_to: str = "",
        site_name: str = "",
        site_url: str = "",
        support_email: str = "",
        contact_wechat: str = "",
        tls: str = "starttls",
        timeout: float = 12.0,
    ) -> None:
        if not host or not sender:
            raise MailerError("SMTP 未配置")
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.sender = sender
        self.sender_name = sender_name or site_name or "PyShield 授权中心"
        self.reply_to = reply_to
        self.site_name = site_name or "PyShield 授权中心"
        self.site_url = site_url
        self.support_email = support_email
        self.contact_wechat = contact_wechat
        self.tls = tls.lower()
        self.timeout = timeout

    def _send(self, message: EmailMessage) -> None:
        try:
            if self.tls == "ssl":
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context) as client:
                    self._login(client)
                    client.send_message(message)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as client:
                    client.ehlo()
                    if self.tls == "starttls":
                        context = ssl.create_default_context()
                        client.starttls(context=context)
                        client.ehlo()
                    self._login(client)
                    client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailerError(f"邮件发送失败：{exc}") from exc

    def _login(self, client: smtplib.SMTP) -> None:
        if self.username:
            try:
                client.login(self.username, self.password)
            except smtplib.SMTPException as exc:
                raise MailerError(f"SMTP 登录失败：{exc}") from exc

    def send_code(self, email: str, code: str, purpose: str = "trial") -> None:
        rendered = render_code_email(
            code=code,
            site_name=self.site_name,
            site_url=self.site_url,
            support_email=self.support_email,
            contact_wechat=self.contact_wechat,
        )
        self._send(
            self._message(email, rendered.subject, rendered.text, rendered.html),
        )

    def send_notice(self, email: str, subject: str, text: str) -> None:
        rendered = render_notice_email(
            subject=subject,
            text=text,
            site_name=self.site_name,
            site_url=self.site_url,
            support_email=self.support_email,
            contact_wechat=self.contact_wechat,
        )
        self._send(
            self._message(email, rendered.subject, rendered.text, rendered.html),
        )

    def _message(self, email: str, subject: str, text: str, html: str) -> EmailMessage:
        message = EmailMessage()
        message["From"] = formataddr((self.sender_name, self.sender))
        message["To"] = email
        message["Subject"] = " ".join(str(subject).split()) or "通知"
        if self.reply_to:
            message["Reply-To"] = self.reply_to
        message["Auto-Submitted"] = "auto-generated"
        message["X-Auto-Response-Suppress"] = "All"
        message.set_content(text)
        if html:
            message.add_alternative(html, subtype="html")
        return message


class MissingMailer(Mailer):
    def send_code(self, email: str, code: str, purpose: str = "trial") -> None:
        raise MailerError("邮件服务未配置，请联系管理员")

    def send_notice(self, email: str, subject: str, text: str) -> None:
        raise MailerError("邮件服务未配置，请联系管理员")


def build_mailer(config: Config) -> Mailer:
    if config.mailer_is_configured:
        return SMTPMailer(
            host=config.smtp_host,
            port=config.smtp_port,
            username=config.smtp_user,
            password=config.smtp_password,
            sender=config.smtp_from,
            sender_name=config.smtp_from_name,
            reply_to=config.smtp_reply_to,
            site_name=config.site_name,
            site_url=config.site_url,
            support_email=config.support_email,
            contact_wechat=config.contact_wechat,
            tls=config.smtp_tls,
        )
    if config.dev:
        return ConsoleMailer()
    return MissingMailer()
