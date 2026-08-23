from __future__ import annotations

from html import escape

import httpx

from .config import settings


class EmailDeliveryError(RuntimeError):
    pass


def email_configured() -> bool:
    """True only when the HTTPS SendGrid configuration is complete."""
    return bool(settings.sendgrid_api_key.strip() and settings.email_from_email.strip())


def email_provider() -> str:
    return "sendgrid" if email_configured() else "none"


def _temporary_password_content(to_email: str, temporary_password: str, expiry_minutes: int) -> tuple[str, str, str]:
    subject = "OPR Android - Kata laluan sementara"
    plain = (
        "Permintaan lupa kata laluan diterima.\n\n"
        f"Kata laluan sementara: {temporary_password}\n"
        f"Sah selama {expiry_minutes} minit.\n\n"
        "Log masuk menggunakan kata laluan sementara ini. Aplikasi akan meminta anda mencipta kata laluan baharu.\n"
        "Jika anda tidak meminta tetapan semula kata laluan, abaikan e-mel ini."
    )
    html = f"""<!doctype html>
<html><body style="margin:0;background:#f6f3f6;font-family:Arial,sans-serif;color:#172033">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:28px 12px">
<table role="presentation" width="560" cellspacing="0" cellpadding="0" style="max-width:560px;background:#fff;border-radius:16px;overflow:hidden">
<tr><td style="background:#f3c528;padding:22px 28px;border-bottom:7px solid #11213f">
<div style="font-size:30px;font-weight:800;color:#fff;letter-spacing:1px">ONE PAGE</div>
<div style="font-size:34px;font-weight:700;color:#ff2b20;font-style:italic">Report</div>
</td></tr>
<tr><td style="padding:28px">
<h2 style="margin:0 0 12px;color:#11213f">Tetapan semula kata laluan</h2>
<p>Permintaan lupa kata laluan untuk akaun <b>{escape(to_email)}</b> telah diterima.</p>
<p style="margin-top:22px">Kata laluan sementara anda:</p>
<div style="background:#11213f;color:#fff;font-size:24px;font-weight:800;letter-spacing:2px;text-align:center;padding:16px;border-radius:10px">{escape(temporary_password)}</div>
<p style="margin-top:18px">Kata laluan ini sah selama <b>{expiry_minutes} minit</b>. Selepas log masuk, aplikasi akan meminta anda menetapkan kata laluan baharu.</p>
<p style="color:#64748b;font-size:13px">Jika anda tidak membuat permintaan ini, abaikan e-mel ini.</p>
</td></tr>
<tr><td style="background:#11213f;color:#fff;padding:14px 28px;font-size:12px">OPR Android · AI Billing Server</td></tr>
</table></td></tr></table></body></html>"""
    return subject, plain, html


def _sendgrid_payload(to_email: str, temporary_password: str, expiry_minutes: int) -> dict:
    subject, plain, html = _temporary_password_content(to_email, temporary_password, expiry_minutes)
    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {
            "email": settings.email_from_email.strip(),
            "name": settings.email_from_name.strip() or "OPR Android",
        },
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": plain},
            {"type": "text/html", "value": html},
        ],
    }
    reply_to = settings.email_reply_to.strip()
    if reply_to:
        payload["reply_to"] = {"email": reply_to}
    return payload


def send_temporary_password(to_email: str, temporary_password: str, expiry_minutes: int) -> None:
    """Send reset email using SendGrid v3 Mail Send over HTTPS."""
    if not email_configured():
        raise EmailDeliveryError(
            "E-mel belum dikonfigurasi. Tetapkan SENDGRID_API_KEY dan EMAIL_FROM_EMAIL di Railway."
        )

    base = settings.sendgrid_api_base.rstrip("/")
    url = f"{base}/v3/mail/send"
    headers = {
        "Authorization": f"Bearer {settings.sendgrid_api_key.strip()}",
        "Content-Type": "application/json",
    }
    payload = _sendgrid_payload(to_email, temporary_password, expiry_minutes)

    try:
        with httpx.Client(timeout=settings.email_timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise EmailDeliveryError(f"Gagal menghubungi perkhidmatan e-mel melalui HTTPS: {exc}") from exc

    if not 200 <= response.status_code < 300:
        detail = response.text.strip()
        if len(detail) > 1200:
            detail = detail[:1200] + "..."
        raise EmailDeliveryError(
            f"SendGrid menolak e-mel (HTTP {response.status_code}): {detail or 'tiada butiran'}"
        )
