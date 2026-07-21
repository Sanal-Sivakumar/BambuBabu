"""
BambuBabu — Email Mailer
Sends Gmail SMTP notifications for all print lifecycle events.
"""
from __future__ import annotations
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

from backend.config import settings
from backend.core.logger import get_logger

if TYPE_CHECKING:
    from backend.db.models import Job

log = get_logger("bambububu.mailer")

PRINTER_NAMES = {
    "p1s":     "Bambu Lab P1S",
    "a1_mini": "Bambu Lab A1 Mini",
}


def _send(to: str | list[str], subject: str, html_body: str) -> None:
    """Core SMTP send function."""
    if not settings.SMTP_PASSWORD:
        log.warning("SMTP_PASSWORD not set — skipping email")
        return

    recipients = [to] if isinstance(to, str) else to

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"BambuBabu 🐼 <{settings.SMTP_USER}>"
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_USER, recipients, msg.as_string())
        log.info(f"Email sent → {recipients} | {subject}")
    except Exception as e:
        log.error(f"Email failed: {e}")


# ── Public email functions ──────────────────────────────────────────────────

def send_print_started(job: "Job", printer_id: str) -> None:
    printer_name = PRINTER_NAMES.get(printer_id, printer_id)
    est = f"~{job.estimated_minutes} min" if job.estimated_minutes else "unknown"
    html = _template(
        title="🖨️ Your Print Has Started!",
        color="#00c896",
        body=f"""
        <p>Great news, <strong>{job.user_name}</strong>!</p>
        <p>Your file <strong>{job.original_filename}</strong> has started printing.</p>
        <table>
          <tr><td>🖨️ Printer</td><td><strong>{printer_name}</strong></td></tr>
          <tr><td>⏱️ Estimated Time</td><td><strong>{est}</strong></td></tr>
          <tr><td>📋 Job ID</td><td><code>{job.id[:8]}</code></td></tr>
        </table>
        <p>You'll receive another email when it's done.</p>
        """,
    )
    _send(job.user_email, f"🖨️ Printing started — {job.original_filename}", html)


def send_print_complete(job: "Job", printer_id: str) -> None:
    printer_name = PRINTER_NAMES.get(printer_id, printer_id)
    html = _template(
        title="✅ Print Complete!",
        color="#7c3aed",
        body=f"""
        <p>Hi <strong>{job.user_name}</strong>,</p>
        <p>Your print is done! Come collect it from the printer.</p>
        <table>
          <tr><td>📄 File</td><td><strong>{job.original_filename}</strong></td></tr>
          <tr><td>🖨️ Printer</td><td><strong>{printer_name}</strong></td></tr>
          <tr><td>📋 Job ID</td><td><code>{job.id[:8]}</code></td></tr>
        </table>
        """,
    )
    _send(job.user_email, f"✅ Print complete — {job.original_filename}", html)

    # Also notify admin to clear the plate
    admin_html = _template(
        title="🗑️ Plate Clearance Required",
        color="#f59e0b",
        body=f"""
        <p>The following print has completed and the plate needs to be cleared
        before the next job can start.</p>
        <table>
          <tr><td>📄 File</td><td><strong>{job.original_filename}</strong></td></tr>
          <tr><td>👤 User</td><td>{job.user_name} ({job.user_email})</td></tr>
          <tr><td>🖨️ Printer</td><td><strong>{printer_name}</strong></td></tr>
        </table>
        <p>Please remove the model from the plate and click
        <strong>Plate Cleared</strong> in the BambuBabu dashboard.</p>
        """,
    )
    _send(settings.ADMIN_EMAIL,
          f"🗑️ Plate clearance needed — {printer_name}", admin_html)


def send_print_failed(job: "Job", printer_id: str) -> None:
    printer_name = PRINTER_NAMES.get(printer_id, printer_id)
    html = _template(
        title="❌ Print Failed",
        color="#ef4444",
        body=f"""
        <p>Unfortunately, the print for <strong>{job.user_name}</strong> has failed.</p>
        <table>
          <tr><td>📄 File</td><td><strong>{job.original_filename}</strong></td></tr>
          <tr><td>🖨️ Printer</td><td><strong>{printer_name}</strong></td></tr>
          <tr><td>❗ Error</td><td>{job.error_message or "Unknown error"}</td></tr>
        </table>
        <p>Please check the printer and clear the plate before the next job
        can run.</p>
        """,
    )
    recipients = list({job.user_email, settings.ADMIN_EMAIL})
    _send(recipients, f"❌ Print failed — {job.original_filename}", html)


# ── HTML template ───────────────────────────────────────────────────────────

def _template(title: str, color: str, body: str) -> str:
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0f0f17;font-family:Arial,sans-serif;">
  <div style="max-width:560px;margin:32px auto;background:#1a1a2e;border-radius:12px;
              overflow:hidden;border:1px solid #2a2a3e;">
    <!-- Header -->
    <div style="background:{color};padding:24px 32px;">
      <h1 style="margin:0;color:#fff;font-size:22px;font-weight:700;">
        🐼 BambuBabu
      </h1>
      <p style="margin:8px 0 0;color:rgba(255,255,255,0.85);font-size:14px;">
        Automated 3D Print Management
      </p>
    </div>
    <!-- Body -->
    <div style="padding:28px 32px;color:#e2e2e8;font-size:15px;line-height:1.6;">
      <h2 style="margin:0 0 16px;color:#fff;font-size:18px;">{title}</h2>
      <style>
        table {{ width:100%;border-collapse:collapse;margin:12px 0; }}
        td {{ padding:8px 12px;border-bottom:1px solid #2a2a3e; }}
        td:first-child {{ color:#888;width:40%; }}
        code {{ background:#0f0f17;padding:2px 6px;border-radius:4px;
                font-family:monospace;font-size:13px; }}
      </style>
      {body}
    </div>
    <!-- Footer -->
    <div style="padding:16px 32px;background:#13131f;color:#555;font-size:12px;">
      BambuBabu — Raspberry Pi 3D Print Automation
    </div>
  </div>
</body>
</html>
"""
