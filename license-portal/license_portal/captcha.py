"""Human-verification support (Cloudflare Turnstile)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .config import Config

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class CaptchaError(Exception):
    """Raised when a captcha token is missing or rejected."""


class Captcha:
    enabled = False
    site_key = ""

    def verify(self, token: str, remote_ip: str = "") -> None:
        return None


class NullCaptcha(Captcha):
    enabled = False
    site_key = ""


class TurnstileCaptcha(Captcha):
    enabled = True

    def __init__(self, site_key: str, secret: str, timeout: float = 6.0) -> None:
        if not site_key or not secret:
            raise CaptchaError("Turnstile 未配置")
        self.site_key = site_key
        self.secret = secret
        self.timeout = timeout

    def verify(self, token: str, remote_ip: str = "") -> None:
        token = str(token or "").strip()
        if not token:
            raise CaptchaError("请完成人机校验")
        payload = {"secret": self.secret, "response": token}
        if remote_ip:
            payload["remoteip"] = remote_ip
        body = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(
            TURNSTILE_VERIFY_URL,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "PyShieldLicensePortal/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                document = json.loads(response.read(64 * 1024).decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise CaptchaError("人机校验服务不可用，请稍后重试") from exc
        if not isinstance(document, dict) or not document.get("success"):
            raise CaptchaError("人机校验未通过，请重新验证")


def build_captcha(config: Config) -> Captcha:
    if config.turnstile_site_key and config.turnstile_secret:
        return TurnstileCaptcha(config.turnstile_site_key, config.turnstile_secret)
    return NullCaptcha()
