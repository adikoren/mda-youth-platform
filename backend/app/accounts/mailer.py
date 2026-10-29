"""Outgoing email for password reset. Three modes (MDA_EMAIL_MODE):

- gmail:   sends through the Gmail API over HTTPS, as the dedicated Gmail account, with OAuth (gmail.send scope)
           and a refresh token. Gmail answers "accepted" (queued); that is not proof of delivery, so the result is
           reported as accepted, never as delivered.
- mailjet: sends through Mailjet's HTTPS API (send API v3.1) with an API key and secret. Mailjet answers "success"
           when it accepts the message; that is not proof of delivery, so the result is reported as accepted.
- capture: writes each message to MDA_EMAIL_CAPTURE_DIR as a JSON file. Local development only.
- off:     sends nothing. Callers keep answering neutrally, and the failure is logged without any secret.

Secrets (MDA_GMAIL_CLIENT_SECRET, MDA_GMAIL_REFRESH_TOKEN, the short-lived access token) are read from settings or
memory only and are never logged or returned.
"""
import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

import httpx

from app.core.config import get_settings

log = logging.getLogger("mda.mailer")
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
MAILJET_URL = "https://api.mailjet.com/v3.1/send"

# In-process access token, reused until shortly before it expires (Google issues them for one hour).
_token_cache: dict = {"value": "", "expires_at": 0.0}


class MailNotConfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class SendResult:
    mode: str
    status: str  # "accepted" (Gmail queued it) or "captured" (written to the local folder)


def _access_token(s) -> str:
    now = time.time()
    if _token_cache["value"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["value"]
    try:
        r = httpx.post(GOOGLE_TOKEN_URL, timeout=10, data={
            "client_id": s.gmail_client_id,
            "client_secret": s.gmail_client_secret,
            "refresh_token": s.gmail_refresh_token,
            "grant_type": "refresh_token",
        })
    except httpx.HTTPError as exc:
        raise MailNotConfigured(f"gmail: token service unreachable: {type(exc).__name__}") from None
    if r.status_code != 200:
        raise MailNotConfigured(f"gmail: token refresh refused (HTTP {r.status_code})")
    body = r.json()
    _token_cache["value"] = body["access_token"]
    _token_cache["expires_at"] = now + int(body.get("expires_in", 3600))
    return _token_cache["value"]


def _raw_message(s, to: str, subject: str, text: str, html: str) -> str:
    msg = EmailMessage()
    msg["From"] = formataddr((s.email_from_name, s.email_from_address))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def _mailjet_accepted(r) -> bool:
    try:
        return r.json()["Messages"][0]["Status"] == "success"
    except (ValueError, KeyError, IndexError, TypeError):
        return False


def send(to: str, subject: str, text: str, html: str) -> SendResult:
    s = get_settings()
    if s.email_mode == "gmail":
        if not (s.gmail_client_id and s.gmail_client_secret and s.gmail_refresh_token and s.email_from_address):
            raise MailNotConfigured("gmail: MDA_GMAIL_CLIENT_ID, MDA_GMAIL_CLIENT_SECRET, MDA_GMAIL_REFRESH_TOKEN "
                                    "or MDA_EMAIL_FROM_ADDRESS is not set")
        token = _access_token(s)
        raw = _raw_message(s, to, subject, text, html)
        try:
            r = httpx.post(GMAIL_SEND_URL, json={"raw": raw}, timeout=10,
                           headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            raise MailNotConfigured(f"gmail unreachable: {type(exc).__name__}") from None
        if r.status_code == 401:
            _token_cache["value"] = ""  # revoked or expired early: fetch a new token next time
        if r.status_code != 200:
            raise MailNotConfigured(f"gmail refused the message (HTTP {r.status_code})")
        return SendResult(mode="gmail", status="accepted")
    if s.email_mode == "mailjet":
        if not (s.mailjet_api_key and s.mailjet_api_secret and s.email_from_address):
            raise MailNotConfigured("mailjet: MDA_MAILJET_API_KEY, MDA_MAILJET_API_SECRET or MDA_EMAIL_FROM_ADDRESS "
                                    "is not set")
        payload = {"Messages": [{
            "From": {"Email": s.email_from_address, "Name": s.email_from_name},
            "To": [{"Email": to}],
            "Subject": subject,
            "TextPart": text,
            "HTMLPart": html,
        }]}
        try:
            r = httpx.post(MAILJET_URL, json=payload, timeout=10, auth=(s.mailjet_api_key, s.mailjet_api_secret))
        except httpx.HTTPError as exc:
            raise MailNotConfigured(f"mailjet unreachable: {type(exc).__name__}") from None
        if r.status_code != 200 or not _mailjet_accepted(r):
            raise MailNotConfigured(f"mailjet refused the message (HTTP {r.status_code})")
        return SendResult(mode="mailjet", status="accepted")
    if s.email_mode == "capture":
        folder = Path(s.email_capture_dir or Path.home() / "mda-local-data" / "outbox")
        folder.mkdir(parents=True, exist_ok=True)
        item = {"to": to, "subject": subject, "text": text, "html": html, "at": datetime.now().isoformat()}
        (folder / f"{time.time_ns():020d}-{uuid.uuid4().hex[:8]}.json").write_text(json.dumps(item, ensure_ascii=False, indent=2))
        return SendResult(mode="capture", status="captured")
    raise MailNotConfigured("email is off (MDA_EMAIL_MODE=off)")
