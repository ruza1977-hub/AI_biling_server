import os
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")
os.environ.setdefault("MASTER_ADMIN_KEY", "test-admin")
os.environ.setdefault("SENDGRID_API_KEY", "SG.test")
os.environ.setdefault("EMAIL_FROM_EMAIL", "opr@example.com")

from app.email_service import _sendgrid_payload, email_configured

assert email_configured()
p = _sendgrid_payload("guru@example.com", "TempA1!pass", 30)
assert p["personalizations"][0]["to"][0]["email"] == "guru@example.com"
assert p["from"]["email"] == "opr@example.com"
assert len(p["content"]) == 2
assert "TempA1!pass" in p["content"][0]["value"]
print("SendGrid payload OK")
