import json
import logging
import os
import uuid
from datetime import UTC, datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

ses = boto3.client("ses")
SENDER = os.environ.get("SENDER_EMAIL", "")


def build_html(subject, body):
    """Build professional HTML email."""
    return f"""<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
<div style="background:#1a365d;padding:20px;color:white;text-align:center;">
<h2 style="margin:0;">SecureGuard Insurance</h2>
</div>
<div style="padding:20px;border:1px solid #e2e8f0;">
<h3>{subject}</h3>
{body.replace(chr(10), "<br>")}
</div>
<div style="padding:10px;background:#f7fafc;text-align:center;font-size:12px;color:#718096;">
<p>SecureGuard Insurance | Claims Department<br>
This is an automated notification. Do not reply to this email.</p>
</div>
</body></html>"""


def handler(event, context):
    recipient_email = event.get("recipient_email", "")
    subject = event.get("subject", "")
    body = event.get("body", "")

    if not recipient_email or not subject:
        return json.dumps({"error": "recipient_email and subject are required"})

    message_id = f"MSG-{uuid.uuid4().hex[:8].upper()}"
    timestamp = datetime.now(UTC).isoformat()

    if os.environ.get("NOTIFICATIONS_ENABLED", "false") != "true":
        return json.dumps({"status": "suppressed", "message_id": message_id})

    if SENDER:
        try:
            ses.send_email(
                Source=SENDER,
                Destination={"ToAddresses": [recipient_email]},
                Message={
                    "Subject": {"Data": f"[SecureGuard] {subject}"},
                    "Body": {
                        "Text": {"Data": body},
                        "Html": {"Data": build_html(subject, body)},
                    },
                },
            )
            status = "sent"
        except Exception:  # noqa: BLE001 -- report send failure without exposing recipient or service response
            logger.error("SES send failed")
            status = "ses_error"
    else:
        logger.info("Email draft prepared (SES not configured)")
        status = "draft_logged"

    return json.dumps(
        {
            "message_id": message_id,
            "recipient": recipient_email,
            "subject": subject,
            "status": status,
            "sent_at": timestamp,
        }
    )
