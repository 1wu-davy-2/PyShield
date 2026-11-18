"""Browser JSON API for the React console in ``license-portal/web``.

The desktop client contract (:mod:`license_portal.server`, ``/api/trial/*`` and
``/api/license/*``) is intentionally separate and must stay stable.  Everything
here is consumed by the single page app only:

* ``/api/public/*`` — landing page config, trial application, e-mail
  verification, upgrade requests and activation-code redemption.
* ``/api/admin/*``  — session protected console endpoints.  Every mutating
  request additionally requires the ``X-CSRF-Token`` header that was handed
  out by :func:`login`.

The module contains no HTTP plumbing; :mod:`license_portal.server` owns the
socket, cookies and headers.  Handlers return :class:`ApiResponse` objects and
raise :class:`~license_portal.service.ServiceError` or
:class:`~license_portal.validation.ValidationError` for failures.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field

from . import __version__, validation
from .config import Config
from .service import PortalService, ServiceError

PUBLIC_PREFIX = "/api/public"
ADMIN_PREFIX = "/api/admin"

_EDITIONS = ("trial", "pro", "enterprise")
_LICENSE_STATUSES = ("active", "expired", "revoked")
_KEY_STATUSES = ("unused", "active", "revoked", "expired")
_ORDER_STATUSES = ("pending", "paid", "fulfilled", "cancelled")


# ---------------------------------------------------------------------------
# request / response containers
# ---------------------------------------------------------------------------


@dataclass
class ApiRequest:
    """A normalised API request produced by the HTTP layer."""

    path: str
    query: dict
    payload: dict
    ip: str = ""
    user_agent: str = ""
    method: str = "GET"
    session: dict | None = None
    csrf_header: str = ""


@dataclass
class ApiResponse:
    """A JSON body plus optional cookie side effects."""

    body: dict
    status: int = 200
    session_token: str | None = None
    clear_session: bool = False
    headers: dict = field(default_factory=dict)


def handle(service: PortalService, config: Config, request: ApiRequest) -> ApiResponse:
    """Route ``request`` to a handler.  Raises on business failures."""

    if request.path.startswith(ADMIN_PREFIX):
        return _handle_admin(service, config, request)
    if request.path.startswith(PUBLIC_PREFIX):
        return _handle_public(service, config, request)
    raise ServiceError("unknown endpoint", 404, "not_found")


# ---------------------------------------------------------------------------
# public endpoints
# ---------------------------------------------------------------------------


def _handle_public(service: PortalService, config: Config, request: ApiRequest) -> ApiResponse:
    path = request.path[len(PUBLIC_PREFIX):] or "/"
    query = request.query
    method = request.method

    if method == "GET":
        if path == "/config":
            return ApiResponse(_public_config(service, config))
        if path == "/license":
            result = service.license_status(
                str(query.get("serial") or ""), str(query.get("machine") or ""),
            )
            return ApiResponse(_public_license(result))
        if path == "/trial/application":
            return ApiResponse(_public_application(service, query))
        raise ServiceError("unknown endpoint", 404, "not_found")

    if path == "/trial/apply":
        if str(request.payload.get("website") or "").strip():
            # Honeypot: answer exactly like a rejected form so bots learn nothing.
            raise ServiceError("表单校验未通过，请重新填写", 400, "spam_rejected")
        result = service.start_trial(
            request.payload, request.ip, request.user_agent,
        )
        return ApiResponse(_trial_step(result))

    if path == "/trial/resend":
        data = validation.validate_resend_request(request.payload)
        result = service.resend_trial_code(data["application_id"], request.ip)
        return ApiResponse(result)

    if path == "/trial/verify":
        data = validation.validate_email_code(request.payload)
        result = service.verify_trial(data["application_id"], data["code"], request.ip)
        return ApiResponse(_public_license(result))

    if path == "/upgrade":
        result = service.create_upgrade_request(request.payload, request.ip)
        return ApiResponse(result)

    if path == "/redeem":
        result = service.activate_key(
            str(request.payload.get("key") or ""),
            str(request.payload.get("serial") or ""),
            str(request.payload.get("machine") or ""),
            request.ip,
        )
        return ApiResponse(_public_license(result))

    raise ServiceError("unknown endpoint", 404, "not_found")


def _public_config(service: PortalService, config: Config) -> dict:
    return {
        "ok": True,
        "site_name": config.site_name,
        "support_email": config.support_email,
        "contact_wechat": config.contact_wechat,
        "trial_days": config.trial_days,
        "trial_builds": config.trial_builds,
        "captcha_site_key": config.turnstile_site_key if config.captcha_enabled else "",
        "email_verification": config.email_verification_enabled,
        "key_redemption_days": config.key_redemption_days,
        "editions": [
            {"edition": "trial", "name": "免费版", "note": "评估加密效果与集成流程"},
            {"edition": "pro", "name": "专业版", "note": "独立开发者与小团队交付"},
            {"edition": "enterprise", "name": "企业版", "note": "多席位团队与离线交付"},
        ],
        "version": __version__,
        "server_time": _now(),
    }


def _trial_step(result: dict) -> dict:
    """A trial application either issues a licence or asks for an e-mail code."""

    if result.get("verification_required"):
        return {
            "ok": True,
            "verification_required": True,
            "application_id": int(result.get("application_id") or 0),
            "email": str(result.get("email") or ""),
            "expires_in": int(result.get("expires_in") or 0),
        }
    return _public_license(result)


def _public_application(service: PortalService, query: dict) -> dict:
    """Metadata for the verification screen; never exposes the code itself."""

    try:
        application_id = int(str(query.get("id") or "").strip())
    except ValueError as exc:
        raise ServiceError("申请编号无效", 400, "invalid_application") from exc
    application = service.get_application(application_id) if application_id > 0 else None
    if application is None:
        raise ServiceError("申请记录不存在或已失效", 404, "application_not_found")
    return {
        "ok": True,
        "application_id": application_id,
        "email": validation.mask_email(str(application.get("customer_email") or "")),
        "status": str(application.get("status") or ""),
        "edition": str(application.get("edition") or "trial"),
        "serial": str(application.get("serial") or ""),
        "updated_at": application.get("updated_at"),
        "code_expires": application.get("code_expires"),
    }


def _public_license(result: dict) -> dict:
    """Trim a licence result down to what a browser page may display.

    The signed ``document`` is deliberately dropped: it is the credential the
    desktop client installs and the browser has no use for it.
    """

    payload = {
        "ok": bool(result.get("ok", True)),
        "status": str(result.get("status") or ""),
        "serial": str(result.get("serial") or ""),
        "machine": str(result.get("machine") or ""),
        "license_id": str(result.get("license_id") or ""),
        "edition": str(result.get("edition") or ""),
        "expires": result.get("expires"),
        "max_builds": result.get("max_builds"),
        "revision": int(result.get("revision") or 0),
        "trial_code": str(result.get("trial_code") or ""),
        "deep_link": str(result.get("deep_link") or ""),
        "already": bool(result.get("already")),
        "reason": str(result.get("reason") or ""),
        "verification_required": False,
    }
    if not payload["deep_link"].startswith("pyshield://"):
        payload["deep_link"] = ""
    return payload


# ---------------------------------------------------------------------------
# admin endpoints
# ---------------------------------------------------------------------------


def _handle_admin(service: PortalService, config: Config, request: ApiRequest) -> ApiResponse:
    path = request.path[len(ADMIN_PREFIX):] or "/"

    if path == "/login":
        if request.method != "POST":
            raise ServiceError("请使用 POST 登录", 405, "method_not_allowed")
        return _login(service, config, request)

    session = request.session
    if session is None:
        raise ServiceError("登录状态已失效，请重新登录", 401, "unauthorized")
    if request.method != "GET":
        _require_csrf(session, request.csrf_header)

    admin = {"username": session.get("username"), "role": session.get("role")}

    if path == "/me":
        return ApiResponse({
            "ok": True, "admin": admin, "csrf_token": session.get("csrf"),
            "expires": session.get("expires"),
        })
    if path == "/logout":
        return ApiResponse({"ok": True}, clear_session=True)

    if path == "/dashboard":
        data = service.dashboard()
        data["expiring"] = [_slim_license(item) for item in data.get("expiring") or []]
        return ApiResponse({"ok": True, **data})

    if path == "/filters":
        return ApiResponse({
            "ok": True,
            "editions": list(_EDITIONS),
            "license_statuses": list(_LICENSE_STATUSES),
            "key_statuses": list(_KEY_STATUSES),
            "order_statuses": list(_ORDER_STATUSES),
            "audit_actions": _audit_actions(service),
        })

    if path == "/licenses":
        items = service.list_licenses(
            search=str(request.query.get("q") or ""),
            edition=str(request.query.get("edition") or ""),
            status=str(request.query.get("status") or ""),
        )
        return ApiResponse({
            "ok": True, "items": [_slim_license(item) for item in items],
            "total": len(items),
        })

    if path.startswith("/licenses/"):
        return _admin_license(service, request, path)

    if path == "/keys":
        if request.method == "POST":
            result = service.create_license_keys(str(admin["username"]), request.payload)
            return ApiResponse(result, status=201)
        items = service.list_license_keys(
            search=str(request.query.get("q") or ""),
            status=str(request.query.get("status") or ""),
            edition=str(request.query.get("edition") or ""),
        )
        return ApiResponse({"ok": True, "items": items, "total": len(items)})

    if path.startswith("/keys/"):
        return _admin_key(service, request, path, str(admin["username"]))

    if path == "/customers":
        items = service.list_customers(search=str(request.query.get("q") or ""))
        return ApiResponse({"ok": True, "items": items, "total": len(items)})

    if path.startswith("/customers/"):
        return _admin_customer(service, request, path)

    if path == "/orders":
        items = service.list_orders(status=str(request.query.get("status") or ""))
        return ApiResponse({"ok": True, "items": items, "total": len(items)})

    if path.startswith("/orders/"):
        return _admin_order(service, request, path, str(admin["username"]))

    if path == "/client-checks":
        limit = _positive_int(request.query.get("limit"), 200, maximum=1000)
        items = service.list_client_checks(
            serial=str(request.query.get("serial") or ""),
            kind=str(request.query.get("kind") or ""),
            ok=str(request.query.get("ok") or ""),
            limit=limit,
        )
        return ApiResponse({"ok": True, "items": items, "total": len(items)})

    if path == "/audit":
        limit = _positive_int(request.query.get("limit"), 200, maximum=1000)
        items = service.list_events(
            action=str(request.query.get("action") or ""),
            search=str(request.query.get("q") or ""),
            limit=limit,
        )
        return ApiResponse({
            "ok": True, "items": [_slim_event(item) for item in items],
            "total": len(items),
        })

    if path == "/signing":
        data = service.dashboard().get("signing_key") or {}
        return ApiResponse({
            "ok": True,
            "signing_key": data,
            "build_command": (
                "python tools/build_commercial.py build "
                "--public-key <root.pub> --site https://你的域名"
            ),
            "prepare_command": (
                "python tools/build_commercial.py prepare --public-key root.pub"
            ),
        })

    raise ServiceError("unknown endpoint", 404, "not_found")


def _login(service: PortalService, config: Config, request: ApiRequest) -> ApiResponse:
    data = validation.validate_login(request.payload)
    result = service.authenticate(
        data["username"], data["password"], request.ip, request.user_agent,
    )
    return ApiResponse(
        body={
            "ok": True,
            "admin": {
                "username": result["admin"]["username"],
                "role": result["admin"]["role"],
            },
            "csrf_token": result["csrf"],
            "expires": result["expires"],
        },
        session_token=result["token"],
    )


def _require_csrf(session: dict, header_value: str) -> None:
    if not hmac.compare_digest(
        str(session.get("csrf") or ""), str(header_value or ""),
    ):
        raise ServiceError("会话校验失败，请重新登录", 403, "csrf_failed")


def _admin_license(service: PortalService, request: ApiRequest, path: str) -> ApiResponse:
    rest = path[len("/licenses/"):].strip("/")
    action = ""
    for suffix in ("/renew", "/upgrade", "/revoke", "/build-quota", "/delete"):
        if rest.endswith(suffix):
            action = suffix[1:]
            rest = rest[:-len(suffix)]
            break
    serial = rest
    actor = str((request.session or {}).get("username") or "")

    if not action:
        if request.method != "GET":
            raise ServiceError("unknown endpoint", 404, "not_found")
        data = service.get_license(serial)
        if data is None:
            raise ServiceError("授权不存在", 404, "license_not_found")
        detail = _slim_license(data, detail=True)
        detail["activations"] = data.get("activations") or []
        detail["events"] = [_slim_event(item) for item in data.get("events") or []]
        detail["client_checks"] = data.get("client_checks") or []
        return ApiResponse({"ok": True, "license": detail})

    if request.method != "POST":
        raise ServiceError("请使用 POST 提交", 405, "method_not_allowed")

    if action == "renew":
        result = service.renew_license(actor, serial, request.payload)
    elif action == "upgrade":
        result = service.upgrade_license(actor, serial, request.payload)
    elif action == "build-quota":
        license_data = service.set_build_quota(actor, serial, request.payload)
        return ApiResponse({
            "ok": True,
            "license": _slim_license(license_data, detail=True),
            "serial": serial,
            "action": action,
        })
    elif action == "delete":
        result = service.delete_license(
            actor, serial, str(request.payload.get("confirm") or ""),
        )
        return ApiResponse({**result, "action": action})
    else:
        result = service.revoke_license(
            actor, serial, str(request.payload.get("reason") or ""),
        )
    license_data = result.get("license")
    if license_data is None:
        # revoke_license only echoes the serial, so re-read the row for the UI.
        license_data = service.get_license(serial)
    return ApiResponse({
        "ok": True,
        "license": _slim_license(license_data, detail=True) if license_data else None,
        "serial": serial,
        "action": action,
    })


def _admin_key(
    service: PortalService, request: ApiRequest, path: str, actor: str,
) -> ApiResponse:
    rest = path[len("/keys/"):].strip("/")
    action = ""
    for suffix in ("/reveal", "/revoke"):
        if rest.endswith(suffix):
            action = suffix[1:]
            rest = rest[:-len(suffix)]
            break
    try:
        key_id = int(rest)
    except ValueError as exc:
        raise ServiceError("激活码编号无效", 400, "invalid_key_id") from exc

    if not action:
        if request.method != "GET":
            raise ServiceError("unknown endpoint", 404, "not_found")
        data = service.get_license_key(key_id)
        if data is None:
            raise ServiceError("激活码不存在", 404, "key_not_found")
        return ApiResponse({"ok": True, "key": data})

    if request.method != "POST":
        raise ServiceError("请使用 POST 提交", 405, "method_not_allowed")

    if action == "reveal":
        plaintext = service.reveal_license_key(actor, key_id)
        return ApiResponse({"ok": True, "id": key_id, "key": plaintext})
    service.revoke_license_key(actor, key_id, str(request.payload.get("reason") or ""))
    return ApiResponse({"ok": True, "id": key_id, "status": "revoked"})


def _admin_customer(
    service: PortalService, request: ApiRequest, path: str,
) -> ApiResponse:
    rest = path[len("/customers/"):].strip("/")
    if request.method != "GET":
        raise ServiceError("unknown endpoint", 404, "not_found")
    try:
        customer_id = int(rest)
    except ValueError as exc:
        raise ServiceError("客户编号无效", 400, "invalid_customer_id") from exc
    data = service.get_customer(customer_id)
    if data is None:
        raise ServiceError("客户不存在", 404, "customer_not_found")
    return ApiResponse({
        "ok": True,
        "customer": data["customer"],
        "licenses": [_slim_license(item) for item in data["licenses"]],
        "orders": data["orders"],
        "applications": data["applications"],
    })


def _admin_order(
    service: PortalService, request: ApiRequest, path: str, actor: str,
) -> ApiResponse:
    rest = path[len("/orders/"):].strip("/")
    if not rest.endswith("/status"):
        raise ServiceError("unknown endpoint", 404, "not_found")
    if request.method != "POST":
        raise ServiceError("请使用 POST 提交", 405, "method_not_allowed")
    try:
        order_id = int(rest[:-len("/status")].strip("/"))
    except ValueError as exc:
        raise ServiceError("订单编号无效", 400, "invalid_order_id") from exc
    result = service.mark_order(
        actor, order_id,
        str(request.payload.get("status") or ""),
        str(request.payload.get("amount") or ""),
    )
    return ApiResponse(result)


# ---------------------------------------------------------------------------
# serialisation helpers
# ---------------------------------------------------------------------------


def _slim_license(data: dict | None, *, detail: bool = False) -> dict:
    """Drop bulky/secret columns the console never renders."""

    if not data:
        return {}
    keep = (
        "id", "serial", "machine", "license_id", "edition", "seats",
        "max_activations", "max_builds_per_month", "grace_days", "issued",
        "not_before", "expires", "revision", "status", "effective_status",
        "source", "display_code", "created_at", "updated_at", "revoked_at",
        "revoked_reason", "customer_email", "customer_phone", "customer_name",
        "customer_org_type", "customer_company", "key_id",
        "builds_used", "builds_period", "builds_limit", "last_seen_at",
    )
    payload = {key: data.get(key) for key in keep if key in data}
    payload["features"] = list(data.get("features") or [])
    if detail:
        payload["fingerprints"] = list(data.get("fingerprints") or [])
        payload["activation_required"] = bool(data.get("activation_required"))
        payload["has_document"] = bool(data.get("document"))
    return payload


def _slim_event(data: dict) -> dict:
    payload = dict(data)
    detail = payload.get("detail")
    if isinstance(detail, str) and detail:
        try:
            payload["detail"] = json.loads(detail)
        except ValueError:
            pass
    return payload


def _audit_actions(service: PortalService) -> list[str]:
    try:
        rows = service.db.query_all(
            "SELECT DISTINCT action FROM events ORDER BY action",
        )
    except Exception:  # noqa: BLE001 - the filter list is cosmetic
        return []
    return [str(row["action"]) for row in rows if row.get("action")]


def _positive_int(value, default: int, *, maximum: int) -> int:
    try:
        number = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    if number <= 0:
        return default
    return min(number, maximum)


def _now() -> str:
    from .db import now_iso

    return now_iso()
