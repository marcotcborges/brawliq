import os
import smtplib
from email.mime.text import MIMEText


def send_reset_email(to_email: str, username: str, reset_url: str) -> bool:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")

    if not all([host, user, password]):
        return False

    body = f"""Hi {username},

You requested a password reset for your BrawlIQ account.

Click the link below to set a new password (expires in 30 minutes):

{reset_url}

If you didn't request this, you can safely ignore this email.

— BrawlIQ
"""
    msg = MIMEText(body)
    msg["Subject"] = "BrawlIQ — password reset"
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
