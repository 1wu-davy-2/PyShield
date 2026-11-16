"""Branded transactional e-mail templates.

Table layout and inline CSS are used on purpose: external stylesheets, web
fonts and remote images are often blocked or stripped by mail clients.  Every
dynamic value is escaped before it is interpolated into the markup.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, urlencode, urlsplit

INK = "#0b0f18"
LIME = "#c6f24e"
BLUE = "#2b5ce6"
PAPER = "#f6f7fa"
BORDER = "#e7ebf2"
MUTED = "#6b7385"
HINT = "#9aa3b5"
FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',"
    "'PingFang SC',Arial,sans-serif"
)
MONO = "Consolas,'SFMono-Regular',Menlo,'Liberation Mono',monospace"


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    text: str
    html: str


def _site_name(value: str) -> str:
    return str(value or "").strip() or "PyShield 授权中心"


def _safe_url(value: str) -> str:
    candidate = str(value or "").strip()
    if urlsplit(candidate).scheme.lower() not in {"http", "https"}:
        return ""
    return candidate


def _footer_note(site_url: str, support_email: str) -> str:
    if support_email:
        return "此邮件由系统自动发送；如需帮助，请通过上方支持邮箱联系我们。"
    if site_url:
        return "此邮件由系统自动发送；如需帮助，请访问授权中心联系我们。"
    return "此邮件由系统自动发送。"


def _footer_text(site_url: str, support_email: str, contact_wechat: str) -> str:
    lines = []
    if support_email:
        lines.append(f"支持邮箱：{support_email}")
    if contact_wechat:
        lines.append(f"客服微信：{contact_wechat}")
    if site_url:
        lines.append(f"授权中心：{site_url}")
    lines.append(_footer_note(site_url, support_email))
    return "\n".join(lines)


def _shell(
    *,
    site_name: str,
    site_url: str,
    support_email: str,
    contact_wechat: str,
    eyebrow: str,
    heading: str,
    body_html: str,
    preheader: str,
) -> str:
    name_html = html.escape(site_name)
    heading_html = html.escape(heading)
    eyebrow_html = html.escape(eyebrow)
    preheader_html = html.escape(preheader)
    support_html = html.escape(support_email)
    wechat_html = html.escape(contact_wechat)
    url_html = html.escape(site_url, quote=True)
    note_html = html.escape(_footer_note(site_url, support_email))

    contact_rows = []
    if support_email:
        contact_rows.append(
            f'<p style="margin:0 0 4px;font-family:{FONT};font-size:12px;'
            f'line-height:1.7;color:{MUTED};">需要帮助？支持邮箱 '
            f'<a href="mailto:{html.escape(support_email, quote=True)}" '
            f'style="color:{BLUE};text-decoration:none;">{support_html}</a></p>'
        )
    if contact_wechat:
        contact_rows.append(
            f'<p style="margin:0 0 4px;font-family:{FONT};font-size:12px;'
            f'line-height:1.7;color:{MUTED};">客服微信：'
            f'<strong style="color:{INK};">{wechat_html}</strong></p>'
        )
    if not contact_rows:
        contact_rows.append(
            f'<p style="margin:0;font-family:{FONT};font-size:12px;'
            f'line-height:1.7;color:{MUTED};">如需帮助，请通过授权中心页面联系管理员。</p>'
        )
    contact_html = "".join(contact_rows)

    button_html = ""
    if site_url:
        button_html = (
            '<table role="presentation" border="0" cellpadding="0" cellspacing="0" '
            'style="margin:26px 0 0;">'
            f'<tr><td style="background-color:{LIME};border-radius:10px;">'
            f'<a href="{url_html}" style="display:inline-block;padding:12px 22px;'
            f'font-family:{FONT};font-size:14px;font-weight:700;color:{INK};'
            'text-decoration:none;">打开授权中心</a>'
            "</td></tr></table>"
        )

    footer_link = ""
    if site_url:
        footer_link = (
            f'<p style="margin:0 0 4px;font-family:{FONT};font-size:12px;'
            f'line-height:1.7;color:{MUTED};">授权中心：'
            f'<a href="{url_html}" style="color:{BLUE};text-decoration:none;">'
            f"{html.escape(site_url)}</a></p>"
        )

    year = datetime.now().year
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<title>{heading_html}</title>
</head>
<body style="margin:0;padding:0;background-color:#eef1f7;">
<div style="display:none;font-size:1px;color:#eef1f7;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">{preheader_html}</div>
<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color:#eef1f7;">
<tr>
<td align="center" style="padding:32px 14px;">
<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width:560px;border-collapse:separate;border-spacing:0;">
<tr>
<td style="background-color:{INK};padding:26px 30px;border-radius:18px 18px 0 0;">
<table role="presentation" border="0" cellpadding="0" cellspacing="0">
<tr>
<td width="38" height="38" align="center" valign="middle" style="background-color:{LIME};border-radius:10px;font-family:Arial,sans-serif;font-size:20px;line-height:38px;font-weight:800;color:{INK};">P</td>
<td style="padding-left:12px;">
<div style="font-family:{FONT};font-size:18px;line-height:1.3;font-weight:700;color:#ffffff;">{name_html}</div>
<div style="font-family:Arial,sans-serif;font-size:10px;line-height:1.4;letter-spacing:2.4px;color:#8f97ab;margin-top:4px;">LICENSE CENTER</div>
</td>
</tr>
</table>
</td>
</tr>
<tr>
<td style="background-color:#ffffff;padding:34px 30px 30px;">
<div style="font-family:{FONT};font-size:11px;line-height:1.5;letter-spacing:2.2px;font-weight:700;color:{BLUE};">{eyebrow_html}</div>
<h1 style="margin:10px 0 18px;font-family:{FONT};font-size:24px;line-height:1.35;font-weight:700;color:{INK};">{heading_html}</h1>
{body_html}
{button_html}
</td>
</tr>
<tr>
<td style="background-color:{PAPER};border-top:1px solid {BORDER};padding:20px 30px 22px;border-radius:0 0 18px 18px;">
{contact_html}
{footer_link}
<p style="margin:8px 0 0;font-family:{FONT};font-size:11px;line-height:1.7;color:{HINT};">{note_html}</p>
</td>
</tr>
</table>
<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width:560px;">
<tr>
<td align="center" style="padding:16px 8px 0;font-family:{FONT};font-size:11px;line-height:1.6;color:{HINT};">© {year} {name_html} · 授权与签发服务</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>"""


def render_code_email(
    *,
    code: str,
    site_name: str = "",
    site_url: str = "",
    support_email: str = "",
    contact_wechat: str = "",
    expires_minutes: int = 10,
) -> RenderedEmail:
    name = _site_name(site_name)
    url = _safe_url(site_url)
    email = str(support_email or "").strip()
    wechat = str(contact_wechat or "").strip()
    minutes = max(1, int(expires_minutes))
    code_text = str(code or "").strip()
    code_html = html.escape(code_text)
    copy_params = {"minutes": str(minutes), "site": name}
    if email:
        copy_params["support"] = email
    if wechat:
        copy_params["wechat"] = wechat
    copy_url = (
        f"{url.rstrip('/')}/copy-code.html?{urlencode(copy_params)}"
        f"#{quote(code_text, safe='')}"
        if url and code_text else ""
    )

    subject = f"【{name}】邮箱验证码"
    preheader = f"你的验证码已送达，{minutes} 分钟内有效。"
    copy_line = f"复制验证码：{copy_url}\n\n" if copy_url else ""
    text = (
        f"{name}\n\n"
        f"你的邮箱验证码是：{code_text}\n\n"
        f"{copy_line}"
        f"验证码 {minutes} 分钟内有效。请勿转发给他人。\n"
        "如果这不是你本人操作，请忽略这封邮件，你的邮箱不会被绑定。\n\n"
        f"{_footer_text(url, email, wechat)}\n"
    )

    code_cell = (
        f'<td align="center" valign="middle" style="font-family:{MONO};'
        'font-size:34px;line-height:1.25;font-weight:700;letter-spacing:8px;'
        f'color:{INK};user-select:all;-webkit-user-select:all;">{code_html}</td>'
    )
    copy_cell = ""
    copy_hint = ""
    if copy_url:
        copy_cell = (
            '<td align="right" valign="middle" style="padding-left:10px;">'
            f'<a href="{html.escape(copy_url, quote=True)}" target="_blank" '
            'rel="noopener" style="display:inline-block;padding:9px 12px;'
            f'border-radius:9px;background-color:{LIME};font-family:{FONT};'
            f'font-size:12px;font-weight:700;color:{INK};text-decoration:none;'
            'white-space:nowrap;">复制</a></td>'
        )
        copy_hint = (
            f'<p style="margin:0 0 12px;font-family:{FONT};font-size:12px;'
            f'line-height:1.7;color:{HINT};">点击「复制」打开复制页；'
            "也可以直接选中验证码。</p>"
        )

    body_html = (
        f'<p style="margin:0 0 22px;font-family:{FONT};font-size:14px;'
        f'line-height:1.75;color:#5c6475;">你好，以下是本次申请使用的邮箱验证码：</p>'
        '<table role="presentation" border="0" cellpadding="0" cellspacing="0" '
        'width="100%" style="margin:0 0 18px;">'
        '<tr><td style="background-color:#f6f7fa;'
        'border:1px dashed #c8cfdd;border-radius:14px;padding:22px 16px;">'
        '<table role="presentation" border="0" cellpadding="0" cellspacing="0" '
        'width="100%"><tr>'
        f"{code_cell}{copy_cell}"
        "</tr></table>"
        "</td></tr></table>"
        f"{copy_hint}"
        f'<p style="margin:0 0 12px;font-family:{FONT};font-size:14px;'
        f'line-height:1.75;color:#5c6475;">验证码 '
        f'<strong style="color:{INK};">{minutes} 分钟</strong>内有效，'
        "请勿转发给他人。</p>"
        f'<p style="margin:0;font-family:{FONT};font-size:13px;'
        f'line-height:1.75;color:{MUTED};">如果这不是你本人操作，请忽略这封邮件，'
        "你的邮箱不会被绑定。</p>"
    )
    return RenderedEmail(
        subject=subject,
        text=text,
        html=_shell(
            site_name=name,
            site_url=url,
            support_email=email,
            contact_wechat=wechat,
            eyebrow="EMAIL VERIFICATION",
            heading="验证你的邮箱",
            body_html=body_html,
            preheader=preheader,
        ),
    )


def _notice_paragraphs(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = [block.strip() for block in normalized.split("\n\n") if block.strip()]
    if not blocks:
        blocks = ["（无正文）"]
    return "".join(
        f'<p style="margin:0 0 12px;font-family:{FONT};font-size:14px;'
        f'line-height:1.75;color:#5c6475;">'
        f'{html.escape(block).replace(chr(10), "<br>")}</p>'
        for block in blocks
    )


def render_notice_email(
    *,
    subject: str,
    text: str,
    site_name: str = "",
    site_url: str = "",
    support_email: str = "",
    contact_wechat: str = "",
) -> RenderedEmail:
    name = _site_name(site_name)
    url = _safe_url(site_url)
    email = str(support_email or "").strip()
    wechat = str(contact_wechat or "").strip()
    clean_subject = " ".join(str(subject or "系统通知").split()) or "系统通知"
    full_subject = (
        clean_subject if clean_subject.startswith(f"【{name}】")
        else f"【{name}】{clean_subject}"
    )
    body_text = str(text or "").strip() or "（无正文）"
    email_text = f"{name}\n\n{body_text}\n\n{_footer_text(url, email, wechat)}\n"
    return RenderedEmail(
        subject=full_subject,
        text=email_text,
        html=_shell(
            site_name=name,
            site_url=url,
            support_email=email,
            contact_wechat=wechat,
            eyebrow="SYSTEM NOTICE",
            heading=clean_subject,
            body_html=_notice_paragraphs(body_text),
            preheader=body_text[:80],
        ),
    )
