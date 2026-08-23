from __future__ import annotations

from email.message import EmailMessage
from html import escape
import smtplib
import ssl

from .config import settings


class EmailDeliveryError(RuntimeError):
    pass


def email_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from_email)


def send_temporary_password(to_email: str, temporary_password: str, expiry_minutes: int) -> None:
    if not email_configured():
        raise EmailDeliveryError(
            "E-mel belum dikonfigurasi pada server. Tetapkan SMTP_HOST dan SMTP_FROM_EMAIL di Railway."
        )

    subject = "OPR Android - Kata laluan sementara"
    plain = (
        "Permintaan lupa kata laluan diterima.\n\n"
        f"Kata laluan sementara: {temporary_password}\n"
        f"Sah selama {expiry_minutes} minit.\n\n"
        "Log masuk menggunakan kata laluan sementara ini. Aplikasi akan meminta anda mencipta kata laluan baharu.\n"
        "Jika anda tidak meminta tetapan semula kata laluan, abaikan e-mel ini."
    )
    html = f"""<!doctype html>
<html><body style=\"margin:0;background:#f6f3f6;font-family:Arial,sans-serif;color:#172033\">
<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\"><tr><td align=\"center\" style=\"padding:28px 12px\">
<table role=\"presentation\" width=\"560\" cellspacing=\"0\" cellpadding=\"0\" style=\"max-width:560px;background:#fff;border-radius:16px;overflow:hidden\">
<tr><td style=\"background:#f3c528;padding:22px 28px;border-bottom:7px solid #11213f\">
<div style=\"font-size:30px;font-weight:800;color:#fff;letter-spacing:1px\">ONE PAGE</div>
<div style=\"font-size:34px;font-weight:700;color:#ff2b20;font-style:italic\">Report</div>
</td></tr>
<tr><td style=\"padding:28px\">
<h2 style=\"margin:0 0 12px;color:#11213f\">Tetapan semula kata laluan</h2>
<p>Permintaan lupa kata laluan untuk akaun <b>{escape(to_email)}</b> telah diterima.</p>
<p style=\"margin-top:22px\">Kata laluan sementara anda:</p>
<div style=\"background:#11213f;color:#fff;font-size:24px;font-weight:800;letter-spacing:2px;text-align:center;padding:16px;border-radius:10px\">{escape(temporary_password)}</div>
<p style=\"margin-top:18px\">Kata laluan ini sah selama <b>{expiry_minutes} minit</b>. Selepas log masuk, aplikasi akan meminta anda menetapkan kata laluan baharu.</p>
<p style=\"color:#64748b;font-size:13px\">Jika anda tidak membuat permintaan ini, abaikan e-mel ini.</p>
</td></tr>
<tr><td style=\"background:#11213f;color:#fff;padding:14px 28px;font-size:12px\">OPR Android · AI Billing Server</td></tr>
</table></td></tr></table></body></html>"""

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    msg["To"] = to_email
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")

    try:
        if settings.smtp_use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20, context=context) as smtp:
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                smtp.ehlo()
                if settings.smtp_starttls:
                    context = ssl.create_default_context()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(msg)
    except Exception as exc:
        raise EmailDeliveryError(f"Gagal menghantar e-mel tetapan semula: {exc}") from exc
