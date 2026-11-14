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
:root {
  --ink: #18202e; --ink2: #475467; --ink3: #7b8494; --ink4: #98a2b3;
  --canvas: #f5f6f8; --card: #ffffff; --line: #e6e8ee;
  --primary: #4b63e8; --primary-deep: #3c52d4; --primary-soft: #eef1fd;
  --ok: #039855; --ok-soft: #e7f6ef; --ok-ink: #067647;
  --warn-soft: #fef4e6; --warn-ink: #b54708;
  --violet: #7a5af8; --violet-soft: #f4f0fe;
  --radius: 14px; --ctrl: 8px;
  --shadow: 0 0 0 1px rgba(16, 24, 40, .06), 0 1px 2px rgba(16, 24, 40, .04);
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--canvas); color: var(--ink); line-height: 1.6;
  font-family: "Noto Sans SC", "Segoe UI", "Microsoft YaHei", "PingFang SC", system-ui, sans-serif;
  font-size: 14px; -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 820px; margin: 0 auto; padding: 52px 24px 64px; }
.brand { display: flex; align-items: center; gap: 10px; margin-bottom: 34px; }
.brand .mark {
  width: 36px; height: 36px; border-radius: 8px; background: var(--primary);
  display: grid; place-items: center; color: #fff; flex: none;
}
.brand .mark svg { width: 20px; height: 20px; }
.brand strong { font-size: 15px; font-weight: 600; }
h1 { margin: 0 0 8px; font-size: 26px; font-weight: 600; letter-spacing: -.01em; }
h2 { margin: 28px 0 12px; font-size: 16px; font-weight: 600; }
p.lead { margin: 0 0 24px; color: var(--ink3); font-size: 13.5px; }
.card {
  background: var(--card); border-radius: var(--radius); padding: 20px;
  box-shadow: var(--shadow); margin-bottom: 16px;
}
.meta { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 0; }
.meta > div { background: #f4f6fa; border-radius: 10px; padding: 12px 14px; }
.meta dt { font-size: 11.5px; color: var(--ink3); margin-bottom: 4px; }
.meta dd { margin: 0; font-size: 13.5px; }
.mono { font-family: Consolas, "Cascadia Mono", ui-monospace, monospace; letter-spacing: .4px; }
.pill {
  display: inline-flex; align-items: center; height: 24px; padding: 0 10px;
  border-radius: 999px; font-size: 11.5px; font-weight: 500;
  background: var(--canvas); color: var(--ink2);
}
.pill.ok { background: var(--ok-soft); color: var(--ok-ink); }
.pill.warn { background: var(--warn-soft); color: var(--warn-ink); }
.plans { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin: 0 0 20px; }
.plan {
  background: var(--card); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 20px; display: flex; flex-direction: column; gap: 12px; box-shadow: var(--shadow);
}
.plan.is-current { border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-soft); }
.plan header { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.plan h3 { margin: 0; font-size: 15px; font-weight: 600; }
.tag { padding: 2px 8px; border-radius: 999px; font-size: 11.5px; font-weight: 500;
  background: var(--primary-soft); color: var(--primary-deep); white-space: nowrap; }
.tag.ok { background: var(--ok-soft); color: var(--ok-ink); }
.tag.violet { background: var(--violet-soft); color: var(--violet); }
.price { margin: 0; font-size: 12.5px; color: var(--ink3); }
.plan ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column;
  gap: 8px; font-size: 12.5px; color: var(--ink2); }
.plan li { position: relative; padding-left: 18px; }
.plan li::before { content: "✓"; position: absolute; left: 0; color: var(--primary);
  font-size: 12px; font-weight: 600; }
.plan .note { margin: auto 0 0; padding-top: 12px; border-top: 1px dashed var(--line);
  font-size: 11.5px; color: var(--ink4); }
.plan form { margin: 0; }
.btn {
  display: inline-flex; align-items: center; justify-content: center; height: 40px;
  padding: 0 16px; border: 0; border-radius: var(--ctrl); font-size: 13.5px;
  font-weight: 500; background: var(--primary); color: #fff; text-decoration: none;
  cursor: pointer; font-family: inherit; transition: background .15s ease;
}
.btn:hover { background: var(--primary-deep); }
.btn.ghost { background: var(--canvas); color: var(--ink2); }
.btn.ghost:hover { background: var(--line); }
.btn.block { width: 100%; }
.btn.lg { height: 46px; font-size: 14.5px; padding: 0 24px; }
.row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.field { display: flex; gap: 10px; margin-top: 8px; }
.input {
  flex: 1; min-width: 0; height: 40px; padding: 0 12px; border: 1px solid var(--line);
  border-radius: var(--ctrl); font-size: 13.5px; font-family: inherit; outline: none;
  background: var(--card); color: var(--ink);
}
.input:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(75, 99, 232, .12); }
.note { font-size: 12px; color: var(--ink4); margin-top: 14px; }
.footer { margin-top: 34px; text-align: center; font-size: 12px; color: var(--ink4); }
@media (max-width: 720px) {
  .wrap { padding: 32px 18px 48px; }
  .meta, .plans { grid-template-columns: 1fr; }
  h1 { font-size: 22px; }
}
"""

_MARK = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M12 2.9l7.1 2.7v5.6c0 4.4-2.9 7.8-7.1 9.1C7.9 19 5 15.6 5 11.2V5.6L12 2.9z" '
    'fill="currentColor" opacity=".18"/>'
    '<path d="M12 2.9l7.1 2.7v5.6c0 4.4-2.9 7.8-7.1 9.1C7.9 19 5 15.6 5 11.2V5.6L12 2.9z" '
    'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>'
    '<path d="M10.7 9l-2.4 2.6 2.4 2.6M13.3 9l2.4 2.6-2.4 2.6" fill="none" stroke="currentColor" '
    'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · PyShield</title>
<style>{_STYLE}</style>
</head><body><div class="wrap">
<div class="brand"><span class="mark">{_MARK}</span><strong>PyShield</strong></div>
{body}
<div class="footer">PyShield {html.escape(__version__)} · 序列号仅绑定本机，更换设备需重新申请</div>
</div></body></html>"""


def _landing_page() -> str:
    body = """
<h1>申请 PyShield 试用</h1>
<p class="lead">请从 PyShield 加密构建器的「版本与试用」页面点击对应版本，系统会自动携带本机序列号打开本页。</p>
<div class="card">
  <form method="get" action="/activate">
    <label for="serial" style="font-size:12px;color:var(--ink3)">已有序列号？</label>
    <div class="field">
      <input class="input mono" id="serial" name="serial" placeholder="PS-XXXX-XXXX-XXXX" autocomplete="off">
      <button class="btn" type="submit">继续</button>
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
        f'<span class="pill ok">已开通 · 到期 {html.escape(str(record.get("expires") or ""))}</span>'
        if granted else '<span class="pill warn">待申请</span>'
    )
    body = f"""
<h1>PyShield 版本与试用</h1>
<p class="lead">本页已绑定你的本机序列号，申请后授权会自动回传到桌面应用。</p>

<div class="card">
  <dl class="meta">
    <div><dt>序列号</dt><dd class="mono">{html.escape(serial)}</dd></div>
    <div><dt>机器码</dt><dd class="mono">{html.escape(short_machine)}</dd></div>
    <div><dt>状态</dt><dd>{status}</dd></div>
  </dl>
</div>

<h2>选择版本</h2>
<div class="plans">
  <div class="plan {'is-current' if edition == 'trial' else ''}">
    <header><h3>免费版</h3><span class="tag ok">立即可用</span></header>
    <p class="price">14 天 · 3 次加密</p>
    <ul>
      <li>完整加密与签名清单</li>
      <li>ChaCha20 纯 Python 运行时</li>
      <li>绑定本机，到期自动失效</li>
    </ul>
    <p class="note">不含原生运行时、产物签发与 CI</p>
    <form method="post" action="/trial/apply">
      <input type="hidden" name="serial" value="{html.escape(serial)}">
      <input type="hidden" name="machine" value="{html.escape(machine)}">
      <input type="hidden" name="edition" value="trial">
      <button class="btn block" type="submit">{'查看已开通授权' if granted else '申请 14 天免费试用'}</button>
    </form>
  </div>
  <div class="plan {'is-current' if edition == 'pro' else ''}">
    <header><h3>专业版</h3><span class="tag violet">按席位</span></header>
    <p class="price">年订阅 · 月度构建配额</p>
    <ul>
      <li>原生运行时（Cython 编译）</li>
      <li>产物许可证签发与续期</li>
      <li>多项目交付与团队协作</li>
    </ul>
    <p class="note">需要商务开通，官网申请报价</p>
    <form method="post" action="/trial/apply">
      <input type="hidden" name="serial" value="{html.escape(serial)}">
      <input type="hidden" name="machine" value="{html.escape(machine)}">
      <input type="hidden" name="edition" value="pro">
      <button class="btn ghost block" type="submit">了解专业版</button>
    </form>
  </div>
  <div class="plan {'is-current' if edition == 'enterprise' else ''}">
    <header><h3>企业版</h3><span class="tag violet">多席位</span></header>
    <p class="price">私有部署 · 离线激活</p>
    <ul>
      <li>离线激活与 CI 构建令牌</li>
      <li>撤销列表、审计与配额管理</li>
      <li>可对接内部签发服务</li>
    </ul>
    <p class="note">支持私有化部署与定制交付</p>
    <form method="post" action="/trial/apply">
      <input type="hidden" name="serial" value="{html.escape(serial)}">
      <input type="hidden" name="machine" value="{html.escape(machine)}">
      <input type="hidden" name="edition" value="enterprise">
      <button class="btn ghost block" type="submit">联系企业版</button>
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
  <dl class="meta">
    <div><dt>序列号</dt><dd class="mono">{html.escape(serial)}</dd></div>
    <div><dt>到期时间</dt><dd>{html.escape(str(expires))}</dd></div>
    <div><dt>可用时长</dt><dd>{days} 天 · {builds} 次构建</dd></div>
  </dl>
</div>

<div class="card">
  <p style="margin:0 0 14px;font-size:13.5px">
    <strong>点击下面的按钮回到 PyShield。</strong>
    浏览器如果弹出「是否打开 PyShield」，请选择允许。
  </p>
  <a class="btn lg" id="openApp" href="{html.escape(link)}">打开 PyShield 并激活</a>
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
  <dl class="meta">
    <div><dt>序列号</dt><dd class="mono">{html.escape(serial)}</dd></div>
    <div><dt>意向版本</dt><dd>{html.escape(title)}</dd></div>
    <div><dt>后续</dt><dd>商务会与你确认席位与部署方式</dd></div>
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
