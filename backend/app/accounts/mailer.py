"""Outgoing email for password reset. Three modes (MDA_EMAIL_MODE):

- brevo:   sends through Brevo's transactional API. Brevo answers "accepted" (queued); that is not proof of delivery,
           so the result is reported as accepted, never as delivered.
- capture: writes each message to MDA_EMAIL_CAPTURE_DIR as a JSON file. Local development only.
- off:     sends nothing. Callers keep answering neutrally, and the failure is logged without any secret.

Secrets (BREVO_API_KEY) are read from settings only and are never logged or returned.
"""
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from app.core.config import get_settings

log = logging.getLogger("mda.mailer")
BREVO_URL = "https://api.brevo.com/v3/smtp/email"


class MailNotConfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class SendResult:
    mode: str
    status: str  # "accepted" (Brevo queued it) or "captured" (written to the local folder)


def send(to: str, subject: str, text: str, html: str) -> SendResult:
    s = get_settings()
    if s.email_mode == "brevo":
        if not s.brevo_api_key or not s.email_from_address:
            raise MailNotConfigured("brevo: BREVO_API_KEY or MDA_EMAIL_FROM_ADDRESS is not set")
        payload = {
            "sender": {"email": s.email_from_address, "name": s.email_from_name},
            "to": [{"email": to}],
            "subject": subject,
            "htmlContent": html,
            "textContent": text,
        }
        try:
            r = httpx.post(BREVO_URL, json=payload, timeout=10,
                           headers={"api-key": s.brevo_api_key, "accept": "application/json"})
        except httpx.HTTPError as exc:
            raise MailNotConfigured(f"brevo unreachable: {type(exc).__name__}") from None
        if r.status_code >= 300:
            raise MailNotConfigured(f"brevo refused the message (HTTP {r.status_code})")
        return SendResult(mode="brevo", status="accepted")
    if s.email_mode == "capture":
        folder = Path(s.email_capture_dir or Path.home() / "mda-local-data" / "outbox")
        folder.mkdir(parents=True, exist_ok=True)
        item = {"to": to, "subject": subject, "text": text, "html": html, "at": datetime.now().isoformat()}
        (folder / f"{time.time_ns():020d}-{uuid.uuid4().hex[:8]}.json").write_text(json.dumps(item, ensure_ascii=False, indent=2))
        return SendResult(mode="capture", status="captured")
    raise MailNotConfigured("email is off (MDA_EMAIL_MODE=off)")
