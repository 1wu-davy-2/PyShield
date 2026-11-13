"""Trial registration site and API for the PyShield desktop builder.

Flow:

1. The builder derives a serial from the machine fingerprint and opens
   ``/activate?serial=...`` in the browser.
2. The visitor picks an edition and submits the form; the server stores the
   request in SQLite and signs a machine-bound trial entitlement.
3. The page redirects to ``pyshield://activate?serial=...`` so the desktop app
   regains focus, while the app also polls ``/api/trial/status`` as a fallback.

Only the trial edition is issued automatically. Pro and Enterprise are contact
links, because they require a commercial agreement.

Run it with::

    python -m pyshield.trial_server --root-key root-keys/root.key \
        --data-dir trial-data --host 127.0.0.1 --port 8090
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, ed25519, license_admin, tool_license, trial_license

DEFAULT_TRIAL_DAYS = 14
DEFAULT_TRIAL_BUILDS = 3
DEFAULT_TRIAL_FEATURES = (tool_license.FEATURE_GUI,)
MAX_BODY = 64 * 1024
RATE_LIMIT_PER_HOUR = 12

EDITION_TITLES = {
    "trial": "免费版 · 14 天试用",
    "pro": "专业版",
    "enterprise": "企业版",
}


class TrialServerError(Exception):
    """Raised for controlled API errors."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> date:
    return date.today()


class TrialStore:
    """SQLite backed registry of trial requests."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "trials.sqlite3"
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS trials (
                    serial TEXT PRIMARY KEY,
                    machine TEXT NOT NULL,
                    edition TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    granted_at TEXT,
                    expires TEXT,
                    max_builds INTEGER NOT NULL DEFAULT 0,
                    document TEXT,
                    remote TEXT,
                    user_agent TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL,
                    serial TEXT,
                    machine TEXT,
                    action TEXT NOT NULL,
                    remote TEXT,
                    detail TEXT
                );
                """
            )
            self._connection.commit()

    def get(self, serial: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM trials WHERE serial = ?", (serial,)
            ).fetchone()
        return dict(row) if row else None

    def upsert_pending(self, serial: str, machine: str, edition: str, remote: str, agent: str) -> dict:
        existing = self.get(serial)
        if existing is not None:
            return existing
        now = _now()
        with self._lock:
            self._connection.execute(
                "INSERT INTO trials (serial, machine, edition, status, created_at, updated_at, remote, user_agent)"
                " VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)",
                (serial, machine, edition, now, now, remote, agent),
            )
            self._connection.commit()
        return self.get(serial) or {}

    def mark_granted(self, serial: str, document: dict, expires: date, builds: int) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE trials SET status = 'granted', granted_at = ?, expires = ?, max_builds = ?,"
                " document = ?, updated_at = ? WHERE serial = ?",
                (
                    _now(), expires.isoformat(), builds,
                    json.dumps(document, sort_keys=True, ensure_ascii=False), _now(), serial,
                ),
            )
            self._connection.commit()

    def log(self, action: str, serial: str, machine: str, remote: str, detail: str = "") -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO events (at, serial, machine, action, remote, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (_now(), serial, machine, action, remote, detail),
            )
            self._connection.commit()

    def recent(self, remote: str, action: str, hours: int = 1) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM events WHERE remote = ? AND action = ? AND at >= ?",
                (remote, action, cutoff),
            ).fetchone()
        return int(row["total"] if row else 0)

    def count(self) -> dict:
        with self._lock:
            rows = self._connection.execute(
                "SELECT status, COUNT(*) AS total FROM trials GROUP BY status"
            ).fetchall()
        return {row["status"]: int(row["total"]) for row in rows}

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _trial_days() -> int:
    try:
        return max(1, min(int(os.environ.get("PYSHELD_TRIAL_DAYS", DEFAULT_TRIAL_DAYS)), 365))
    except ValueError:
        return DEFAULT_TRIAL_DAYS


def _trial_builds() -> int:
    try:
        return max(1, min(int(os.environ.get("PYSHELD_TRIAL_BUILDS", DEFAULT_TRIAL_BUILDS)), 1000))
    except ValueError:
        return DEFAULT_TRIAL_BUILDS


class TrialServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], root_key: bytes, data_dir: str | Path) -> None:
        self.root_key = root_key
        self.public_key = ed25519.public_key(root_key)
        self.store = TrialStore(data_dir)
        super().__init__(address, TrialHandler)


class TrialHandler(BaseHTTPRequestHandler):
    server: TrialServer
    server_version = "PyShieldTrialServer/1.0"

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"[trial-server] {self.address_string()} {fmt % args}\n")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _html(self, status: HTTPStatus, body: str) -> None:
        self._send(status, body.encode("utf-8"), "text/html; charset=utf-8")

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"error": message})

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise TrialServerError("Content-Length 无效") from exc
        if length <= 0 or length > MAX_BODY:
            raise TrialServerError("请求体为空或过大")
        return self.rfile.read(length)

    def _read_json(self) -> dict:
        try:
            document = json.loads(self._read_body().decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise TrialServerError("请求体不是合法 JSON") from exc
        if not isinstance(document, dict):
            raise TrialServerError("请求体必须是 JSON 对象")
        return document

    def _read_form(self) -> dict:
        try:
            fields = parse_qs(self._read_body().decode("utf-8"), keep_blank_values=True)
        except (ValueError, UnicodeDecodeError) as exc:
            raise TrialServerError("表单数据无法解析") from exc
        return {key: values[0] for key, values in fields.items() if values}

    # -- routing ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items() if values}
        try:
            if parsed.path == "/":
                self._html(HTTPStatus.OK, _landing_page())
            elif parsed.path == "/activate":
                self._activate_page(query)
            elif parsed.path == "/api/trial/status":
                self._status(query)
            elif parsed.path == "/health":
                self._json(HTTPStatus.OK, {"ok": True, "service": "pyshield-trial", "time": _now()})
            else:
                self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
        except TrialServerError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/trial/apply":
                self._apply(self._read_form(), form=True)
            elif parsed.path == "/api/trial/apply":
                self._apply(self._read_json(), form=False)
            elif parsed.path == "/api/trial/claim":
                self._claim(self._read_json())
            else:
                self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
        except TrialServerError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except tool_license.ToolLicenseError as exc:
            self._error(HTTPStatus.FORBIDDEN, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    # -- handlers ---------------------------------------------------------

    def _activate_page(self, query: dict) -> None:
        raw_serial = str(query.get("serial") or "")
        edition = str(query.get("edition") or "trial").strip().lower()
        if edition not in EDITION_TITLES:
            edition = "trial"
        if not raw_serial:
            self._html(HTTPStatus.OK, _landing_page())
            return
        try:
            serial = trial_license.normalize_serial(raw_serial)
        except trial_license.TrialError as exc:
            self._html(HTTPStatus.BAD_REQUEST, _message_page("序列号无效", str(exc)))
            return
        machine = str(query.get("machine") or "").strip().lower()
        record = self.server.store.get(serial)
        if record and machine and record["machine"] != machine:
            self._html(
                HTTPStatus.FORBIDDEN,
                _message_page("序列号已被其他设备使用", "同一序列号只能在一台机器上申请试用。"),
            )
            return
        if record is None:
            record = self.server.store.upsert_pending(
                serial, machine, edition, self.address_string(), self.headers.get("User-Agent", ""),
            )
            self.server.store.log("view", serial, machine, self.address_string())
        self._html(
            HTTPStatus.OK,
            _activate_page(serial=serial, machine=machine or record["machine"], edition=edition, record=record),
        )

    def _apply(self, payload: dict, form: bool) -> None:
        serial_raw = str(payload.get("serial") or "")
        edition = str(payload.get("edition") or "trial").strip().lower()
        machine = str(payload.get("machine") or "").strip().lower()
        if edition not in EDITION_TITLES:
            raise TrialServerError("未知版本")
        try:
            serial = trial_license.normalize_serial(serial_raw)
        except trial_license.TrialError as exc:
            raise TrialServerError(str(exc)) from exc

        record = self.server.store.get(serial)
        if record is not None:
            if machine and record["machine"] and machine != record["machine"]:
                raise TrialServerError("序列号已绑定其他机器")
            machine = record["machine"] or machine
        if not machine:
            raise TrialServerError("缺少机器码，请从软件内打开本页面")

        remote = self.address_string()
        if self.server.store.recent(remote, "apply") >= RATE_LIMIT_PER_HOUR:
            raise TrialServerError("申请过于频繁，请稍后再试")

        if edition != "trial":
            self.server.store.upsert_pending(
                serial, machine, edition, remote, self.headers.get("User-Agent", ""),
            )
            self.server.store.log("contact", serial, machine, remote, edition)
            page = _contact_page(serial, edition)
            self._html(HTTPStatus.OK, page) if form else self._json(
                HTTPStatus.OK, {"ok": False, "edition": edition, "message": "该版本需要联系销售"},
            )
            return

        record = record or self.server.store.upsert_pending(
            serial, machine, edition, remote, self.headers.get("User-Agent", ""),
        )
        if record.get("status") == "granted" and record.get("document"):
            document = json.loads(record["document"])
        else:
            days = _trial_days()
            builds = _trial_builds()
            expires = _today() + timedelta(days=days)
            document = license_admin.issue_entitlement(
                self.server.root_key,
                license_id=f"TRIAL-{serial}",
                customer="试用用户",
                edition=tool_license.EDITION_TRIAL,
                features=list(DEFAULT_TRIAL_FEATURES),
                seats=1,
                max_activations=1,
                max_builds_per_month=builds,
                bind_mode=tool_license.BIND_FIXED,
                fingerprints=[machine],
                activation_required=False,
                expires=expires,
                grace_days=0,
                notes=f"{days} day trial for {serial}",
            )
            self.server.store.mark_granted(serial, document, expires, builds)
            self.server.store.log("grant", serial, machine, remote, f"{days}d/{builds}x")

        link = trial_license.deep_link(serial)
        if form:
            self._html(HTTPStatus.OK, _granted_page(serial, machine, document, link))
        else:
            self._json(HTTPStatus.OK, {"ok": True, "serial": serial, "document": document, "deep_link": link})

    def _claim(self, payload: dict) -> None:
        serial_raw = str(payload.get("serial") or "")
        machine = str(payload.get("machine") or "").strip().lower()
        try:
            serial = trial_license.normalize_serial(serial_raw)
        except trial_license.TrialError as exc:
            raise TrialServerError(str(exc)) from exc
        record = self.server.store.get(serial)
        if record is None:
            raise TrialServerError("没有找到该序列号的申请记录")
        if machine != record["machine"]:
            raise TrialServerError("机器码与申请记录不一致")
        if record["status"] != "granted" or not record.get("document"):
            raise TrialServerError("试用授权尚未签发")
        self.server.store.log("claim", serial, machine, self.address_string())
        self._json(HTTPStatus.OK, {
            "ok": True,
            "serial": serial,
            "document": json.loads(record["document"]),
        })

    def _status(self, query: dict) -> None:
        try:
            serial = trial_license.normalize_serial(str(query.get("serial") or ""))
        except trial_license.TrialError as exc:
            raise TrialServerError(str(exc)) from exc
        record = self.server.store.get(serial)
        if record is None:
            self._json(HTTPStatus.OK, {"ok": True, "serial": serial, "status": "unknown"})
            return
        self._json(HTTPStatus.OK, {
            "ok": True,
            "serial": serial,
            "status": record["status"],
            "edition": record["edition"],
            "expires": record["expires"],
            "max_builds": record["max_builds"],
        })


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------

_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  background: #f5f6f8; color: #1c2330; line-height: 1.6; }
.wrap { max-width: 760px; margin: 0 auto; padding: 48px 24px 64px; }
.brand { display: flex; align-items: center; gap: 10px; font-weight: 600; font-size: 15px; margin-bottom: 28px; }
.brand span.dot { width: 26px; height: 26px; border-radius: 8px; background: #185fa5; display: inline-block; }
h1 { font-size: 26px; margin: 0 0 8px; font-weight: 600; letter-spacing: .2px; }
p.lead { margin: 0 0 24px; color: #5b6472; font-size: 14px; }
.card { background: #fff; border: 1px solid #e3e6ec; border-radius: 14px; padding: 24px; margin-bottom: 18px; }
dl { display: flex; flex-direction: column; gap: 10px; margin: 0; font-size: 14px; }
dl > div { display: grid; grid-template-columns: 88px minmax(0, 1fr); gap: 4px 18px; align-items: baseline; }
dt { color: #7a8496; }
dd { margin: 0; font-variant-numeric: tabular-nums; }
.mono { font-family: ui-monospace, "Cascadia Code", Consolas, monospace; letter-spacing: .5px; }
.plans { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin: 8px 0 22px; }
.plan { background: #fff; border: 1px solid #e3e6ec; border-radius: 14px; padding: 18px; display: flex;
  flex-direction: column; gap: 8px; }
.plan.is-active { border-color: #185fa5; box-shadow: 0 0 0 3px rgba(24,95,165,.1); }
.plan h3 { margin: 0; font-size: 15px; }
.plan .price { font-size: 13px; color: #5b6472; }
.plan ul { margin: 0; padding-left: 18px; font-size: 13px; color: #4a5364; flex: 1; }
button, .btn { appearance: none; border: 0; border-radius: 10px; padding: 10px 16px; font-size: 14px;
  font-weight: 500; cursor: pointer; background: #185fa5; color: #fff; text-decoration: none;
  display: inline-block; text-align: center; }
button.ghost, .btn.ghost { background: #eef1f5; color: #1c2330; }
button:disabled { opacity: .55; cursor: default; }
.note { font-size: 12.5px; color: #7a8496; margin-top: 12px; }
.badge { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 12px;
  background: #e8f0fa; color: #185fa5; }
.ok { color: #0f6e56; }
.warn { color: #854f0b; }
.input { width: 100%; padding: 10px 12px; border: 1px solid #d6dae2; border-radius: 10px; font-size: 14px;
  font-family: inherit; }
.footer { color: #98a1b0; font-size: 12px; margin-top: 28px; text-align: center; }
@media (max-width: 640px) { .plans { grid-template-columns: 1fr; } }
"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · PyShield</title>
<style>{_STYLE}</style>
</head><body><div class="wrap">
<div class="brand"><span class="dot"></span>PyShield</div>
{body}
<div class="footer">PyShield {html.escape(__version__)} · 序列号仅绑定本机，更换设备需重新申请</div>
</div></body></html>"""


def _landing_page() -> str:
    body = """
<h1>申请 PyShield 试用</h1>
<p class="lead">请从 PyShield 加密构建器的「版本与试用」页面点击对应版本，系统会自动携带本机序列号打开本页。</p>
<div class="card">
  <form method="get" action="/activate">
    <label for="serial" style="font-size:14px;color:#5b6472">已有序列号？</label>
    <div style="display:flex;gap:10px;margin-top:8px">
      <input class="input" id="serial" name="serial" placeholder="PS-XXXX-XXXX-XXXX" autocomplete="off">
      <button type="submit">继续</button>
    </div>
  </form>
  <p class="note">序列号由构建器在首次启动时生成，显示在「版本与试用」页面。</p>
</div>
"""
    return _page("申请试用", body)


def _activate_page(serial: str, machine: str, edition: str, record: dict) -> str:
    granted = record.get("status") == "granted"
    short_machine = machine[:16] + "…" if len(machine) > 16 else (machine or "未提供")
    status = (
        f'<span class="badge ok">已开通</span> 到期 {html.escape(str(record.get("expires") or ""))}'
        if granted else '<span class="badge">待申请</span>'
    )
    body = f"""
<h1>PyShield 版本与试用</h1>
<p class="lead">本页已绑定你的本机序列号，申请后授权会自动回传到桌面应用。</p>
<div class="card">
  <dl>
    <div><dt>序列号</dt><dd class="mono">{html.escape(serial)}</dd></div>
    <div><dt>机器码</dt><dd class="mono">{html.escape(short_machine)}</dd></div>
    <div><dt>状态</dt><dd>{status}</dd></div>
  </dl>
</div>

<h2 style="font-size:16px;margin:24px 0 10px">选择版本</h2>
<div class="plans">
  <div class="plan {'is-active' if edition == 'trial' else ''}">
    <h3>免费版</h3>
    <div class="price">14 天 · 3 次加密</div>
    <ul><li>完整加密与完整性清单</li><li>ChaCha20 纯 Python 运行时</li><li>不含原生运行时与产物签发</li></ul>
    <form method="post" action="/trial/apply">
      <input type="hidden" name="serial" value="{html.escape(serial)}">
      <input type="hidden" name="machine" value="{html.escape(machine)}">
      <input type="hidden" name="edition" value="trial">
      <button type="submit">{'查看已开通授权' if granted else '申请 14 天免费试用'}</button>
    </form>
  </div>
  <div class="plan {'is-active' if edition == 'pro' else ''}">
    <h3>专业版</h3>
    <div class="price">按席位年订阅</div>
    <ul><li>原生运行时与产物签发</li><li>每月构建配额</li><li>邮件支持</li></ul>
    <form method="post" action="/trial/apply">
      <input type="hidden" name="serial" value="{html.escape(serial)}">
      <input type="hidden" name="machine" value="{html.escape(machine)}">
      <input type="hidden" name="edition" value="pro">
      <button class="ghost" type="submit">了解专业版</button>
    </form>
  </div>
  <div class="plan {'is-active' if edition == 'enterprise' else ''}">
    <h3>企业版</h3>
    <div class="price">多席位 · 可私有部署</div>
    <ul><li>离线激活与 CI 令牌</li><li>撤销列表与审计</li><li>优先支持</li></ul>
    <form method="post" action="/trial/apply">
      <input type="hidden" name="serial" value="{html.escape(serial)}">
      <input type="hidden" name="machine" value="{html.escape(machine)}">
      <input type="hidden" name="edition" value="enterprise">
      <button class="ghost" type="submit">联系企业版</button>
    </form>
  </div>
</div>
<p class="note">试用授权与本机机器码绑定，无法转移到其他设备；到期后加密构建会被拒绝，但已生成的产物不受影响。</p>
"""
    return _page("版本与试用", body)


def _granted_page(serial: str, machine: str, document: dict, link: str) -> str:
    payload = document.get("payload") or {}
    days = _trial_days()
    builds = payload.get("max_builds_per_month") or _trial_builds()
    expires = payload.get("expires") or ""
    body = f"""
<h1>试用已开通</h1>
<p class="lead">授权已经写入服务端，正在尝试唤起桌面应用完成激活。</p>
<div class="card">
  <dl>
    <div><dt>序列号</dt><dd class="mono">{html.escape(serial)}</dd></div>
    <div><dt>到期时间</dt><dd>{html.escape(str(expires))}</dd></div>
    <div><dt>可用时长</dt><dd>{days} 天</dd></div>
    <div><dt>构建配额</dt><dd>{builds} 次</dd></div>
    <div><dt>机器码</dt><dd class="mono">{html.escape(machine[:16])}…</dd></div>
  </dl>
</div>
<div class="card">
  <p style="margin:0 0 12px"><strong>点击下面的按钮回到 PyShield。</strong>浏览器如果弹出「是否打开 PyShield」，请选择允许。</p>
  <a class="btn" id="openApp" href="{html.escape(link)}" style="font-size:15px;padding:12px 22px">打开 PyShield 并激活</a>
  <p class="note">应用也会自动轮询授权状态；即使浏览器拦截了跳转，桌面应用也会在几秒内自己切到前台并显示「试用版」。</p>
</div>
<script>
  (function () {{
    var link = {json.dumps(link)};
    try {{ window.location.replace(link); }} catch (error) {{}}
    var button = document.getElementById("openApp");
    if (button) button.focus();
  }})();
</script>
"""
    return _page("试用已开通", body)


def _contact_page(serial: str, edition: str) -> str:
    title = EDITION_TITLES.get(edition, "商业版")
    body = f"""
<h1>{html.escape(title)}</h1>
<p class="lead">该版本需要商务确认，请通过官方渠道联系我们，并附上下面的序列号。</p>
<div class="card">
  <dl>
    <div><dt>序列号</dt><dd class="mono">{html.escape(serial)}</dd></div>
    <div><dt>意向版本</dt><dd>{html.escape(title)}</dd></div>
  </dl>
  <p class="note">免费版可以直接在上一页申请，14 天内可完成 3 次加密构建。</p>
</div>
<p><a class="btn ghost" href="/activate?serial={html.escape(serial)}">返回版本选择</a></p>
"""
    return _page(title, body)


def _message_page(title: str, message: str) -> str:
    body = f"""
<h1>{html.escape(title)}</h1>
<p class="lead">{html.escape(message)}</p>
<p><a class="btn ghost" href="/">返回首页</a></p>
"""
    return _page(title, body)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def create_server(host: str, port: int, root_key: bytes, data_dir: str | Path) -> TrialServer:
    return TrialServer((host, port), root_key, data_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pyshield-trial-server", description="PyShield trial registration site.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--root-key", required=True, help="offline root.key used to sign trial entitlements")
    parser.add_argument("--data-dir", default="trial-data")
    args = parser.parse_args(argv)
    try:
        root_key = license_admin.load_root_key(args.root_key)
        server = create_server(args.host, args.port, root_key, args.data_dir)
    except (license_admin.LicenseAdminError, OSError) as exc:
        print(f"[trial-server] ERROR: {exc}", file=sys.stderr)
        return 1
    host, port = server.server_address[:2]
    print(f"PyShield trial server listening on http://{host}:{port}", flush=True)
    print(f"trial: {_trial_days()} days / {_trial_builds()} builds", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
