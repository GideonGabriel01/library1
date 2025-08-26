# mailer.py
"""
Simple SMTP helper that reads SMTP config from database.get_setting
and sends password-change and admin-reset notifications.

Security notes:
- SMTP password is stored in DB settings in plain text here for local/test usage.
  For production, use an OS secret manager or encrypt the DB field.
- If SMTP host/user/password are empty, the send becomes a no-op (safe).
"""

import smtplib
import ssl
from email.message import EmailMessage
import database

def _get_smtp_config():
    host = database.get_setting("smtp_host") or ""
    port = int(database.get_setting("smtp_port") or 0) if database.get_setting("smtp_port") else None
    user = database.get_setting("smtp_user") or ""
    password = database.get_setting("smtp_password") or ""
    return {"host": host, "port": port, "user": user, "password": password}

def send_email(to_address, subject, body, from_address=None):
    cfg = _get_smtp_config()
    if not (cfg["host"] and cfg["port"] and cfg["user"] and cfg["password"]):
        # SMTP not configured — do nothing (avoid raising in UI)
        print("mailer: SMTP not configured, skipping actual send. To enable, set smtp_host/smtp_port/smtp_user/smtp_password in settings.")
        return {"success": False, "message": "SMTP not configured"}

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_address or cfg["user"]
    msg["To"] = to_address
    msg.set_content(body)

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=10) as server:
            server.starttls(context=context)
            server.login(cfg["user"], cfg["password"])
            server.send_message(msg)
        return {"success": True}
    except Exception as e:
        # do not propagate to UI — return error for logging
        return {"success": False, "message": str(e)}

def send_password_change_email(username, to_email):
    if not to_email:
        return {"success": False, "message": "No recipient email"}
    subject = "Your library account password was changed"
    body = (f"Hello {username},\n\n"
            "This is a confirmation that your password was recently changed.\n\n"
            "If you did not request this change, please contact your library admin immediately.\n\n"
            "Regards,\nLibrary System")
    return send_email(to_email, subject, body)

def send_admin_reset_email(admin_username, target_username, to_email):
    if not to_email:
        return {"success": False, "message": "No recipient email"}
    subject = "Your library account password has been reset by admin"
    body = (f"Hello {target_username},\n\n"
            f"Your password was reset by administrator '{admin_username}'.\n"
            "If you did not expect this, please contact your admin and change your password after logging in.\n\n"
            "Regards,\nLibrary System")
    return send_email(to_email, subject, body)
