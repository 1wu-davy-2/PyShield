"""E-mail delivery for verification codes and operational notices."""

from __future__ import annotations

import smtplib
import ssl
import sys
from email.message import EmailMessage

from .config import Config


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
        subject = "PyShield 邮箱验证码"
        text = (
            f"你的 PyShield 验证码是：{code}\n\n"
            "验证码 10 分钟内有效。如果不是你本人操作，请忽略这封邮件。\n"
        )
        html = (
            "<p>你的 PyShield 验证码是：</p>"
            f"<p style=\"font-size:28px;font-weight:600;letter-spacing:4px\">{code}</p>"
            "<p>验证码 10 分钟内有效。如果不是你本人操作，请忽略这封邮件。</p>"
        )
        self._send(self._message(email, subject, text, html))

    def send_notice(self, email: str, subject: str, text: str) -> None:
        self._send(self._message(email, subject, text, ""))

    def _message(self, email: str, subject: str, text: str, html: str) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = email
        message["Subject"] = subject
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
            tls=config.smtp_tls,
        )
    if config.dev:
        return ConsoleMailer()
    return MissingMailer()
