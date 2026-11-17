"""HTTP server for the PyShield license portal.

The process serves three things:

* the desktop-client JSON API — ``/api/trial/*`` and ``/api/license/*``.  This
  contract is consumed by :mod:`pyshield.trial_license` and the desktop GUI and
  must not change;
* the browser JSON API — ``/api/public/*`` and ``/api/admin/*`` — implemented in
  :mod:`license_portal.api`;
* the React console built into ``web/dist`` with history fallback so that
  ``/admin/licenses/PS-...`` also reaches the single page app.

Only the standard library is used.  In production Nginx terminates TLS, serves
``web/dist`` directly and proxies ``/api`` here; the built-in static handler
exists so a single process can also run the whole site for demos and tests.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import mimetypes
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__, api, client_api, security, validation
from .config import Config
from .db import now_iso
from .service import PortalService, ServiceError, create_service

LOGGER = logging.getLogger("license_portal.http")

_MIME_SUFFIXES = {"application/javascript", "image/svg+xml", "application/json"}

_NOT_BUILT_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>前端尚未构建</title>
<style>
  body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f7fa;
       color:#0b0f18;font:15px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
  main{max-width:640px;padding:40px;background:#fff;border:1px solid #e4e8ee;border-radius:16px}
  h1{margin:0 0 10px;font-size:22px}
  p{color:#5c6675;margin:0 0 12px}
  code{background:#eef1f6;padding:2px 6px;border-radius:5px;font-family:ui-monospace,Menlo,monospace}
  pre{margin:16px 0 0;padding:14px 16px;background:#0b0f18;color:#c6f24e;border-radius:10px;
      font-family:ui-monospace,Menlo,monospace;font-size:13px;overflow:auto}
</style></head>
<body><main>
  <h1>前端尚未构建</h1>
  <p>后端已经在运行，但没有找到 <code>web/dist/index.html</code>。</p>
  <p>请先构建 React 控制台，然后刷新本页：</p>
  <pre>cd license-portal/web
npm install
npm run build</pre>
</main></body></html>
"""


def _error_page(status: int, title: str, message: str) -> str:
    """Minimal error document; the SPA owns every user-facing page."""

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{status} · {title}</title>
<style>
  body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f7fa;
       color:#0b0f18;font:15px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
  main{{max-width:560px;padding:36px;background:#fff;border:1px solid #e4e8ee;border-radius:16px}}
  .code{{font-family:ui-monospace,Menlo,monospace;color:#8a93a3;font-size:12px;letter-spacing:.1em}}
  h1{{margin:6px 0 10px;font-size:21px}}
  p{{color:#5c6675;margin:0 0 18px}}
  a{{display:inline-block;padding:9px 16px;border-radius:8px;background:#0b0f18;color:#fff;
     text-decoration:none;font-size:14px}}
</style></head>
<body><main>
  <div class="code">PYSHIELD / {status}</div>
  <h1>{title}</h1>
  <p>{message}</p>
  <a href="/">返回首页</a>
</main></body></html>
"""


class PortalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], config: Config, service: PortalService) -> None:
        self.config = config
        self.service = service
        self.started_at = now_iso()
        super().__init__(address, PortalHandler)


class PortalHandler(BaseHTTPRequestHandler):
    server: PortalServer
    server_version = f"PyShieldLicensePortal/{__version__}"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:
        LOGGER.debug("%s - %s", self.address_string(), fmt % args)

    def log_request(self, code="-", size="-") -> None:
        try:
            path = urlparse(self.path).path
        except Exception:  # noqa: BLE001
            path = "?"
        LOGGER.info("%s %s -> %s", self.command, path, code)

    def _client_ip(self) -> str:
        if self.server.config.trust_proxy:
            forwarded = self.headers.get("X-Forwarded-For", "")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return str(self.client_address[0])

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ServiceError("Content-Length 无效", 400) from exc
        if length <= 0:
            raise ServiceError("请求体为空", 400)
        if length > int(self.server.config.max_body):
            raise ServiceError("请求体过大", 413)
        return self.rfile.read(length)

    def _read_json_optional(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ServiceError("Content-Length 无效", 400) from exc
        if length <= 0:
            return {}
        raw = self._read_body()
        try:
            document = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ServiceError("请求体不是合法 JSON", 400) from exc
        if not isinstance(document, dict):
            raise ServiceError("请求体必须是 JSON 对象", 400) from exc
        return document

    def _query(self) -> dict:
        parsed = urlparse(self.path)
        values = parse_qs(parsed.query, keep_blank_values=True)
        return {key: items[0] for key, items in values.items() if items}

    def _cookies(self) -> dict[str, str]:
        return security.parse_cookies(self.headers.get("Cookie", ""))

    def _cookie(
        self,
        name: str,
        value: str,
        *,
        max_age: int | None = None,
        http_only: bool = True,
        secure: bool | None = None,
    ) -> str:
        parts = [f"{name}={value}", "Path=/", "SameSite=Lax"]
        if http_only:
            parts.append("HttpOnly")
        if secure is None:
            secure = self.server.config.secure_cookies
        if secure:
            parts.append("Secure")
        if max_age is not None:
            parts.append(f"Max-Age={int(max_age)}")
        return "; ".join(parts)

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: list[str] | None = None,
        cache_control: str = "no-store",
    ) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        for key, value in security.security_headers(
            dev=self.server.config.dev, site_url=self.server.config.site_url,
        ).items():
            self.send_header(key, value)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.command != "HEAD" and body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _html(self, body: str, status: int = 200) -> None:
        self._send(status, body.encode("utf-8"), "text/html; charset=utf-8")

    def _json(self, payload: dict, status: int = 200,
              *, cookies: list[str] | None = None) -> None:
        body = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", cookies=cookies)

    def _api_error(self, status: int, message: str, code: str = "error",
                   errors: dict | None = None) -> None:
        payload = {"error": str(message), "code": code}
        if errors:
            payload["errors"] = errors
        self._json(payload, status)

    # -- origin / admin checks -------------------------------------------

    def _origin_ok(self) -> bool:
        source = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if not source:
            # Non-browser clients do not send Origin; rate limits and captcha
            # are the controls that apply to them.
            return True
        parsed = urlparse(source)
        if not parsed.netloc:
            return False
        configured = urlparse(self.server.config.site_url) if self.server.config.site_url else None
        if configured and configured.netloc:
            if parsed.netloc.lower() != configured.netloc.lower():
                return False
            if configured.scheme and parsed.scheme.lower() != configured.scheme.lower():
                return False
            return True
        host = self.headers.get("Host", "")
        return bool(host) and parsed.netloc.lower() == host.lower()

    def _admin_ip_allowed(self) -> bool:
        rules = self.server.config.admin_allow_ips
        if not rules:
            return True
        ip = self._client_ip()
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        for rule in rules:
            if rule == ip:
                return True
            try:
                if address in ipaddress.ip_network(rule, strict=False):
                    return True
            except ValueError:
                continue
        return False

    def _session(self) -> tuple[str, dict | None]:
        token = self._cookies().get(security.session_cookie_name(), "")
        return token, self.server.service.resolve_session(token)

    # -- HTTP verbs -------------------------------------------------------

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        try:
            path = urlparse(self.path).path
            if path.startswith("/api/"):
                self._api(method, path)
                return
            if method == "POST":
                self._api_error(404, "unknown endpoint", "not_found")
                return
            self._get(path)
        except validation.ValidationError as exc:
            if self._wants_json():
                self._api_error(400, exc.message, "validation_failed", exc.errors)
            else:
                self._html(_error_page(400, "参数不正确", exc.message), 400)
        except ServiceError as exc:
            if self._wants_json():
                self._api_error(exc.status, exc.message, exc.code)
            else:
                self._html(_error_page(exc.status, "请求失败", exc.message), exc.status)
        except Exception:  # noqa: BLE001
            LOGGER.error("unhandled request error\n%s", traceback.format_exc())
            if self._wants_json():
                self._api_error(500, "服务器处理请求时出现异常", "internal_error")
            else:
                self._html(
                    _error_page(500, "服务器错误", "服务器处理请求时出现异常，请稍后重试。"),
                    500,
                )

    def _wants_json(self) -> bool:
        return urlparse(self.path).path.startswith("/api/")

    # -- pages ------------------------------------------------------------

    def _get(self, path: str) -> None:
        if path == "/health":
            self._json({
                "ok": True, "service": "pyshield-license-portal",
                "version": __version__, "time": now_iso(),
            })
            return
        if path == "/robots.txt":
            self._send(
                200, b"User-agent: *\nDisallow: /admin\n",
                "text/plain; charset=utf-8", cache_control="public, max-age=3600",
            )
            return
        self._serve_spa(path)

    def _serve_spa(self, path: str) -> None:
        dist = self.server.config.web_dist
        index = dist / "index.html"
        if not index.is_file():
            self._send(503, _NOT_BUILT_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return

        relative = unquote(path).lstrip("/")
        if relative:
            candidate: Path | None
            try:
                candidate = (dist / relative).resolve()
            except OSError:
                candidate = None
            if candidate is not None and candidate.is_file() and candidate.is_relative_to(dist):
                content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
                if content_type.startswith("text/") or content_type in _MIME_SUFFIXES:
                    content_type += "; charset=utf-8"
                cache = (
                    "public, max-age=31536000, immutable"
                    if relative.startswith("assets/")
                    else "public, max-age=3600"
                )
                self._send(200, candidate.read_bytes(), content_type, cache_control=cache)
                return
            if "." in relative.rsplit("/", 1)[-1]:
                # A missing asset should be a real 404, not the SPA shell.
                self._html(_error_page(404, "文件不存在", "没有找到这个静态资源。"), 404)
                return

        self._send(
            200, index.read_bytes(), "text/html; charset=utf-8", cache_control="no-store",
        )

    # -- API routing ------------------------------------------------------

    @staticmethod
    def _is_browser_api(path: str) -> bool:
        return path.startswith(api.PUBLIC_PREFIX) or path.startswith(api.ADMIN_PREFIX)

    def _api(self, method: str, path: str) -> None:
        if self._is_browser_api(path):
            self._browser_api(method, path)
            return
        self._client_api(method, path)

    # ---- desktop client contract (do not change) ------------------------

    def _client_api(self, method: str, path: str) -> None:
        if path in client_api.ENDPOINTS:
            self._client_guard_api(method, path)
            return
        if path == "/api/version" and method in {"GET", "HEAD"}:
            self._json({"ok": True, "version": __version__})
            return
        if path == "/api/license/revocations":
            self._json(self.server.service.revocations_document())
            return
        if path == "/api/trial/status":
            payload = self._query() if method == "GET" else self._read_json_optional()
            try:
                result = self.server.service.trial_status(
                    str(payload.get("serial") or ""),
                    str(payload.get("machine") or ""),
                )
            except validation.ValidationError as exc:
                self._api_error(400, exc.message, "validation_failed", exc.errors)
                return
            except ServiceError as exc:
                self._api_error(exc.status, exc.message, exc.code)
                return
            self._json(result)
            return
        if method == "GET" or method == "HEAD":
            self._api_error(404, "unknown endpoint", "not_found")
            return

        payload = self._read_json_optional()
        if path == "/api/trial/claim":
            try:
                result = self.server.service.claim_trial(
                    str(payload.get("serial") or ""), str(payload.get("machine") or ""),
                )
            except validation.ValidationError as exc:
                self._api_error(400, exc.message, "validation_failed", exc.errors)
                return
            except ServiceError as exc:
                self._api_error(exc.status, exc.message, exc.code)
                return
            self._json(result)
            return
        if path == "/api/license/activate":
            try:
                result = self.server.service.activate_key(
                    str(payload.get("key") or ""),
                    str(payload.get("serial") or ""),
                    str(payload.get("machine") or ""),
                    self._client_ip(),
                )
            except validation.ValidationError as exc:
                self._api_error(400, exc.message, "validation_failed", exc.errors)
                return
            except ServiceError as exc:
                self._api_error(exc.status, exc.message, exc.code)
                return
            self._json(result)
            return
        if path == "/api/license/status":
            try:
                result = self.server.service.license_status(
                    str(payload.get("serial") or ""),
                    str(payload.get("machine") or ""),
                    str(payload.get("license_id") or ""),
                    int(payload.get("revision") or 0),
                    self._client_ip(),
                )
            except (TypeError, ValueError):
                self._api_error(400, "revision 必须是整数", "validation_failed")
                return
            except validation.ValidationError as exc:
                self._api_error(400, exc.message, "validation_failed", exc.errors)
                return
            except ServiceError as exc:
                self._api_error(exc.status, exc.message, exc.code)
                return
            self._json(result)
            return
        self._api_error(404, "unknown endpoint", "not_found")

    def _client_guard_api(self, method: str, path: str) -> None:
        if method != "POST":
            self._api_error(405, "请使用 POST 提交", "method_not_allowed")
            return
        payload = self._read_json_optional()
        try:
            status, body = client_api.handle(
                self.server.service, self.server.config, path, payload, self._client_ip(),
            )
        except validation.ValidationError as exc:
            self._api_error(400, exc.message, "validation_failed", exc.errors)
            return
        except ServiceError as exc:
            self._api_error(exc.status, exc.message, exc.code)
            return
        self._json(body, status=status)

    # ---- browser console ------------------------------------------------

    def _browser_api(self, method: str, path: str) -> None:
        is_admin = path.startswith(api.ADMIN_PREFIX)
        if is_admin and not self._admin_ip_allowed():
            self._api_error(403, "管理端仅允许白名单 IP 访问", "ip_rejected")
            return
        if method == "POST" and not self._origin_ok():
            self._api_error(403, "请求来源校验失败", "origin_rejected")
            return
        try:
            payload = self._read_json_optional() if method == "POST" else {}
        except ServiceError as exc:
            self._api_error(exc.status, exc.message, exc.code)
            return

        token, session = self._session()
        request = api.ApiRequest(
            path=path,
            query=self._query(),
            payload=payload,
            ip=self._client_ip(),
            user_agent=self.headers.get("User-Agent", ""),
            method=method,
            session=session,
            csrf_header=self.headers.get("X-CSRF-Token", ""),
        )
        try:
            result = api.handle(self.server.service, self.server.config, request)
        except validation.ValidationError as exc:
            self._api_error(400, exc.message, "validation_failed", exc.errors)
            return
        except ServiceError as exc:
            self._api_error(exc.status, exc.message, exc.code)
            return
        except Exception:  # noqa: BLE001
            LOGGER.error("unhandled API error for %s\n%s", path, traceback.format_exc())
            self._api_error(500, "服务器处理请求时出现异常", "internal_error")
            return

        cookies: list[str] = []
        if result.clear_session:
            self.server.service.delete_session(token)
            cookies.append(self._cookie(security.session_cookie_name(), "", max_age=0))
        if result.session_token:
            cookies.append(self._cookie(
                security.session_cookie_name(), result.session_token,
                max_age=int(self.server.config.session_hours * 3600),
            ))
        body = result.body if isinstance(result.body, dict) else {"ok": True}
        self._json(body, result.status, cookies=cookies or None)


def create_server(
    config: Config,
    service: PortalService | None = None,
) -> PortalServer:
    if service is None:
        service = create_service(config)
    return PortalServer((config.host, config.port), config, service)


def run(config: Config) -> None:
    server = create_server(config)
    if not server.service.has_admin():
        LOGGER.warning(
            "no administrator exists; create one with: "
            "python -m license_portal admin create --config <config> --username admin",
        )
    if not (config.web_dist / "index.html").is_file():
        LOGGER.warning(
            "frontend build not found at %s; run `npm run build` in license-portal/web",
            config.web_dist,
        )
    host, port = server.server_address[:2]
    LOGGER.info("PyShield license portal listening on http://%s:%s", host, port)
    LOGGER.info("signing key id: %s", server.service.signing_key.key_id)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("shutting down")
    finally:
        server.server_close()
        server.service.db.close()
