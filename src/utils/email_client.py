"""
Low-level email send/receive via Outlook SMTP + IMAP.

Uses Python stdlib only (smtplib, imaplib, email). No pip installs needed.
Designed for ArchiRex@outlook.com with App Password auth.
"""

import email
import email.mime.text
import email.utils
import imaplib
import logging
import smtplib
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Rate limiting: max sends per day (resets at midnight-ish via counter)
_MAX_SENDS_PER_DAY = 20
_send_count = 0
_send_count_date = ""
_send_lock = threading.Lock()


def _check_rate_limit() -> Optional[str]:
    """Check daily send rate limit. Returns error string or None if OK."""
    global _send_count, _send_count_date
    today = time.strftime("%Y-%m-%d")
    with _send_lock:
        if _send_count_date != today:
            _send_count = 0
            _send_count_date = today
        if _send_count >= _MAX_SENDS_PER_DAY:
            return f"Daily email send limit reached ({_MAX_SENDS_PER_DAY}). Try again tomorrow."
        _send_count += 1
    return None


def _contains_secrets(text: str) -> bool:
    """Basic check for leaked secrets in email content."""
    lower = text.lower()
    patterns = (
        "api_key=", "api_secret=", "app_password=", "token=",
        "password=", "secret_key=", "private_key=",
        "archi_email_app_password", "discord_token",
    )
    return any(p in lower for p in patterns)


class EmailClient:
    """Low-level email send/receive via Outlook SMTP + IMAP."""

    def __init__(self, address: str, app_password: str) -> None:
        self.address = address
        self.app_password = app_password
        self.smtp_host = "smtp-mail.outlook.com"
        self.smtp_port = 587  # STARTTLS
        self.imap_host = "outlook.office365.com"
        self.imap_port = 993  # SSL

    def send(self, to: str, subject: str, body: str, html: bool = False) -> Dict:
        """Send an email. Returns {success, message} or {success, error}."""
        # Safety: content guard
        if _contains_secrets(body) or _contains_secrets(subject):
            return {"success": False, "error": "Blocked: email body or subject contains potential secrets/credentials."}

        # Rate limit
        rate_err = _check_rate_limit()
        if rate_err:
            return {"success": False, "error": rate_err}

        try:
            msg = email.mime.text.MIMEText(body, "html" if html else "plain", "utf-8")
            msg["From"] = self.address
            msg["To"] = to
            msg["Subject"] = subject
            msg["Date"] = email.utils.formatdate(localtime=True)

            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(self.address, self.app_password)
                server.send_message(msg)

            logger.info("Email sent to %s: %s", to, subject[:60])
            return {"success": True, "message": f"Email sent to {to}."}
        except smtplib.SMTPAuthenticationError as e:
            logger.error("SMTP auth failed: %s", e)
            return {"success": False, "error": "Authentication failed. Check app password."}
        except smtplib.SMTPException as e:
            logger.error("SMTP error sending to %s: %s", to, e)
            return {"success": False, "error": f"SMTP error: {e}"}
        except Exception as e:
            logger.error("Email send failed: %s", e)
            return {"success": False, "error": f"Send failed: {e}"}

    def read_inbox(self, max_count: int = 10, unread_only: bool = True) -> Dict:
        """Read recent inbox messages. Returns {success, count, messages}."""
        try:
            with imaplib.IMAP4_SSL(self.imap_host, self.imap_port, timeout=30) as conn:
                conn.login(self.address, self.app_password)
                conn.select("INBOX", readonly=True)

                criteria = "UNSEEN" if unread_only else "ALL"
                status, data = conn.search(None, criteria)
                if status != "OK":
                    return {"success": False, "error": f"IMAP search failed: {status}"}

                msg_ids = data[0].split()
                if not msg_ids:
                    return {"success": True, "count": 0, "messages": []}

                # Take the most recent N
                recent_ids = msg_ids[-max_count:]
                messages = []
                for uid in reversed(recent_ids):
                    msg_data = self._fetch_message(conn, uid)
                    if msg_data:
                        messages.append(msg_data)

                return {"success": True, "count": len(messages), "messages": messages}

        except imaplib.IMAP4.error as e:
            logger.error("IMAP error: %s", e)
            return {"success": False, "error": f"IMAP error: {e}"}
        except Exception as e:
            logger.error("Email read failed: %s", e)
            return {"success": False, "error": f"Read failed: {e}"}

    def search(self, query: str, max_count: int = 10) -> Dict:
        """Search inbox by IMAP criteria (e.g., 'FROM user@example.com', 'SUBJECT hello').

        Returns {success, count, messages}.
        """
        try:
            with imaplib.IMAP4_SSL(self.imap_host, self.imap_port, timeout=30) as conn:
                conn.login(self.address, self.app_password)
                conn.select("INBOX", readonly=True)

                status, data = conn.search(None, query)
                if status != "OK":
                    return {"success": False, "error": f"IMAP search failed: {status}"}

                msg_ids = data[0].split()
                if not msg_ids:
                    return {"success": True, "count": 0, "messages": []}

                recent_ids = msg_ids[-max_count:]
                messages = []
                for uid in reversed(recent_ids):
                    msg_data = self._fetch_message(conn, uid)
                    if msg_data:
                        messages.append(msg_data)

                return {"success": True, "count": len(messages), "messages": messages}

        except imaplib.IMAP4.error as e:
            logger.error("IMAP search error: %s", e)
            return {"success": False, "error": f"IMAP error: {e}"}
        except Exception as e:
            logger.error("Email search failed: %s", e)
            return {"success": False, "error": f"Search failed: {e}"}

    def mark_read(self, uid: str) -> Dict:
        """Mark a message as read by sequence number. Returns {success}."""
        try:
            with imaplib.IMAP4_SSL(self.imap_host, self.imap_port, timeout=30) as conn:
                conn.login(self.address, self.app_password)
                conn.select("INBOX")
                conn.store(uid.encode() if isinstance(uid, str) else uid, "+FLAGS", "\\Seen")
                return {"success": True}
        except Exception as e:
            logger.error("Mark read failed for %s: %s", uid, e)
            return {"success": False, "error": f"Mark read failed: {e}"}

    def _fetch_message(self, conn: imaplib.IMAP4_SSL, msg_id: bytes) -> Optional[Dict]:
        """Fetch and parse a single message by sequence number."""
        try:
            status, data = conn.fetch(msg_id, "(RFC822)")
            if status != "OK" or not data or not data[0]:
                return None

            raw = data[0][1]
            msg = email.message_from_bytes(raw)

            # Decode subject
            subject_parts = email.header.decode_header(msg["Subject"] or "")
            subject = ""
            for part, charset in subject_parts:
                if isinstance(part, bytes):
                    subject += part.decode(charset or "utf-8", errors="replace")
                else:
                    subject += part

            # Extract body (prefer plain text)
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ct = part.get_content_type()
                    if ct == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                            break
                if not body:
                    for part in msg.walk():
                        ct = part.get_content_type()
                        if ct == "text/html":
                            payload = part.get_payload(decode=True)
                            if payload:
                                body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                                break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")

            # Truncate body for preview
            preview = body[:500].strip() if body else ""

            return {
                "from": msg["From"] or "",
                "to": msg["To"] or "",
                "subject": subject.strip(),
                "date": msg["Date"] or "",
                "preview": preview,
                "uid": msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id),
            }
        except Exception as e:
            logger.debug("Failed to parse message %s: %s", msg_id, e)
            return None


def _reset_for_testing() -> None:
    """Reset rate limit state — for test isolation only."""
    global _send_count, _send_count_date
    with _send_lock:
        _send_count = 0
        _send_count_date = ""
