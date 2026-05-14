import os
import smtplib
from email.mime.text import MIMEText


def _send(to_email: str, subject: str, body: str) -> bool:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")
    if not all([host, user, password]):
        return False
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_email
    try:
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
        return True
    except Exception:
        return False


def notify_admin(subject: str, body: str) -> None:
    admin = os.getenv("ADMIN_EMAIL")
    if admin:
        _send(admin, f"[BrawlIQ] {subject}", body)


def send_reset_email(to_email: str, username: str, reset_url: str) -> bool:
    body = (
        f"Hi {username},\n\n"
        "You requested a password reset for your BrawlIQ account.\n\n"
        f"Click the link below to set a new password (expires in 30 minutes):\n\n{reset_url}\n\n"
        "If you didn't request this, you can safely ignore this email.\n\n— BrawlIQ"
    )
    return _send(to_email, "BrawlIQ — password reset", body)
