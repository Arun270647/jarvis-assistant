"""Email for Jarvis.

- send_email: sends through the user's SMTP account (settings "email"). The password
  lives in Windows Credential Manager (target "JARVIS/SMTP/<username>"), never in config,
  prompts or logs. It asks for approval unless every recipient is on
  email.trusted_recipients (by default the user's own addresses). Recipients may be
  contact names from user_profile.json "contacts".
- draft_email: opens a pre-filled draft in the user's mail app (mailto:, handled by the
  default mail app or browser). The user presses Send; needs no setup.
"""
import mimetypes
import os
import re
import smtplib
import ssl
import urllib.parse
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Optional

from src.core import credentials
from src.tools.registry import ToolResult, ToolSpec

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
CRED_PREFIX = "JARVIS/SMTP/"
MAX_ATTACH_BYTES = 15 * 1024 * 1024

PRESETS = {
    "zoho-in": {"smtp_host": "smtp.zoho.in", "smtp_port": 465, "security": "ssl"},
    "zoho": {"smtp_host": "smtp.zoho.com", "smtp_port": 465, "security": "ssl"},
    "gmail": {"smtp_host": "smtp.gmail.com", "smtp_port": 465, "security": "ssl"},
    "outlook": {"smtp_host": "smtp.office365.com", "smtp_port": 587, "security": "starttls"},
}


# ---------------------------------------------------------------- credentials (Windows Credential Manager)

def set_password(username: str, password: str) -> None:
    credentials.write(CRED_PREFIX + username, username, password)


def get_password(username: str) -> Optional[str]:
    return credentials.read(CRED_PREFIX + username)


def has_password(username: str) -> bool:
    return bool(username) and get_password(username) is not None


def delete_password(username: str) -> None:
    credentials.delete(CRED_PREFIX + username)


# ---------------------------------------------------------------- recipients

def resolve_recipients(values, contacts: dict) -> tuple:
    """(emails, unknown) from addresses or contact names/aliases."""
    index = {}
    for name, c in (contacts or {}).items():
        email = c.get("email") if isinstance(c, dict) else c
        if not email:
            continue
        index[name.lower()] = email
        for alias in (c.get("aliases", []) if isinstance(c, dict) else []):
            index[alias.lower()] = email
    emails, unknown = [], []
    for v in values or []:
        v = str(v).strip()
        if EMAIL_RE.match(v):
            emails.append(v)
        elif v.lower() in index:
            emails.append(index[v.lower()])
        else:
            unknown.append(v)
    return emails, unknown


def smtp_settings(config: dict) -> dict:
    e = dict(config.get("email", {}) or {})
    preset = PRESETS.get(e.get("preset", ""), {})
    e.update(preset)          # a named preset is authoritative; "custom" uses the fields as entered
    return e


def send_mail(config: dict, to: list, subject: str, body: str, cc: list = None, attachments: list = None,
              reply_to: str = None, password: str = None) -> str:
    e = smtp_settings(config)
    user = e.get("username") or e.get("from_address")
    if not e.get("smtp_host") or not user:
        raise RuntimeError("Email is not set up: fill in Settings > Email (SMTP server and username).")
    pw = password if password is not None else get_password(user)
    if not pw:
        raise RuntimeError("No email password saved. Save your SMTP / app password in Settings > Email "
                           "(it goes to Windows Credential Manager).")
    msg = EmailMessage()
    sender = e.get("from_address") or user
    msg["From"] = formataddr((e.get("from_name") or "", sender))
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    sig = (e.get("signature") or "").strip()
    msg.set_content(body.rstrip() + (f"\n\n{sig}" if sig else "") + "\n")
    for path in attachments or []:
        data = Path(path).read_bytes()
        ctype, _ = mimetypes.guess_type(str(path))
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=Path(path).name)
    host, port, security = e["smtp_host"], int(e.get("smtp_port", 465)), e.get("security", "ssl")
    timeout = float(e.get("timeout_s", 30))
    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
    try:
        if security == "starttls":
            server.starttls(context=ssl.create_default_context())
        if security != "none":
            server.login(user, pw)
        refused = server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass
    if refused:
        raise RuntimeError(f"The server refused: {', '.join(refused)}")
    return msg["Message-ID"]


# ---------------------------------------------------------------- tools

def register_email_tools(reg, profile: dict, config: dict, workspace=None) -> None:
    ecfg = config.get("email", {}) or {}
    if not ecfg.get("enabled", True):
        return
    contacts = profile.get("contacts", {})
    own = [a.lower() for a in (profile.get("user", {}) or {}).get("emails", [])]
    trusted = {a.lower() for a in ecfg.get("trusted_recipients", [])} | set(own)
    contact_list = "; ".join(f"{k} ({v.get('email') if isinstance(v, dict) else v})" for k, v in contacts.items())

    def all_recipients(a):
        to, _ = resolve_recipients(a.get("to", []), contacts)
        cc, _ = resolve_recipients(a.get("cc", []), contacts)
        return [x.lower() for x in to + cc]

    def needs_approval(a) -> bool:
        rec = all_recipients(a)
        return not rec or any(r not in trusted for r in rec)

    def resolve_attachments(a):
        out = []
        for p in a.get("attachments", []) or []:
            if workspace is None:
                raise ValueError("Attachments need the workspace tools")
            path = workspace.resolve(p, a.get("project"))
            if not path.is_file():
                raise ValueError(f"Attachment not found: {path}")
            if path.stat().st_size > MAX_ATTACH_BYTES:
                raise ValueError(f"{path.name} is larger than 15 MB")
            out.append(path)
        return out

    def send(a, c):
        to, unknown_to = resolve_recipients(a["to"], contacts)
        cc, unknown_cc = resolve_recipients(a.get("cc", []), contacts)
        unknown = unknown_to + unknown_cc
        if unknown:
            return ToolResult(ok=False, status="invalid", content=(
                f"Unknown recipient(s): {', '.join(unknown)}. Use an email address or a contact name ({contact_list}). "
                "Ask the user for the address if you don't have it; never guess one."))
        if config.get("dry_run"):
            return ToolResult(ok=True, status="dry_run", dry_run=True,
                              content=f"Would send '{a['subject']}' to {', '.join(to + cc)}")
        try:
            files = resolve_attachments(a)
            mid = send_mail(config, to, a["subject"], a["body"], cc, files, a.get("reply_to"))
        except (smtplib.SMTPAuthenticationError,) as e:
            return ToolResult(ok=False, status="failed", content=f"Email login failed ({e.smtp_code}). Check the "
                                                                  "username and app password in Settings > Email.")
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, TimeoutError, ConnectionError, OSError) as e:
            if isinstance(e, (FileNotFoundError, PermissionError)):
                return ToolResult(ok=False, status="failed", content=str(e))
            return ToolResult(ok=False, status="failed", content=f"Could not reach the mail server: {e}", transient=True)
        except Exception as e:
            return ToolResult(ok=False, status="failed", content=f"Email not sent: {e}")
        names = ", ".join(p.name for p in files)
        return ToolResult(ok=True, content=f"Sent '{a['subject']}' to {', '.join(to)}" + (f" (cc {', '.join(cc)})" if cc else "")
                                           + (f" with {names}" if names else "") + f". Message-ID {mid}",
                          data={"message_id": mid, "to": to, "cc": cc})

    def draft(a, c):
        to, unknown = resolve_recipients(a.get("to", []), contacts)
        if unknown:
            return ToolResult(ok=False, status="invalid", content=f"Unknown recipient(s): {', '.join(unknown)}")
        query = urllib.parse.urlencode({"subject": a.get("subject", ""), "body": a.get("body", "")},
                                       quote_via=urllib.parse.quote)
        url = f"mailto:{','.join(to)}?{query}"
        if len(url) > 1900:
            return ToolResult(ok=False, status="failed", content="The draft is too long for a mail link; use send_email "
                                                                 "or a shorter body.")
        if config.get("dry_run"):
            return ToolResult(ok=True, status="dry_run", dry_run=True, content=f"Would open a draft to {', '.join(to)}")
        os.startfile(url)   # ShellExecute: opens the default mail handler, no console
        return ToolResult(ok=True, content=f"Opened a draft to {', '.join(to) or '(no recipient)'} in the mail app; "
                                           "the user reviews it and presses Send.")

    def rows(a):
        to, _ = resolve_recipients(a.get("to", []), contacts)
        cc, _ = resolve_recipients(a.get("cc", []), contacts)
        body = a.get("body", "")
        out = [{"k": "To", "v": ", ".join(to) or "(none)", "sub": ""}]
        if cc:
            out.append({"k": "Cc", "v": ", ".join(cc), "sub": ""})
        out.append({"k": "Subject", "v": a.get("subject", ""), "sub": ""})
        out.append({"k": "Body", "v": body[:600] + ("…" if len(body) > 600 else ""), "sub": ""})
        if a.get("attachments"):
            out.append({"k": "Files", "v": ", ".join(map(str, a["attachments"])), "sub": ""})
        return out

    addr_list = {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 20}
    reg.register(ToolSpec(
        name="send_email",
        description=("Send an email from the user's account (" + (ecfg.get("from_address") or "configured in Settings")
                     + "). Recipients: email addresses or contact names (" + contact_list + "). Write it in the user's "
                     "voice, plain text, concise, signed as the user. The user approves it unless every recipient is on "
                     "the trusted list. Never invent addresses."),
        input_schema={"type": "object", "properties": {
            "to": dict(addr_list, minItems=1), "cc": addr_list,
            "subject": {"type": "string", "minLength": 1, "maxLength": 300},
            "body": {"type": "string", "minLength": 1, "maxLength": 20000},
            "attachments": {"type": "array", "items": {"type": "string"}, "maxItems": 10,
                            "description": "Files inside authorized project folders"},
            "project": {"type": "string", "description": "Project key for relative attachment paths"},
            "reply_to": {"type": "string"}},
            "required": ["to", "subject", "body"]},
        handler=send, risk="sensitive", idempotent=False, needs_confirmation=needs_approval, approval_rows=rows,
        retries=1, timeout_s=90, category="action",
        purpose=lambda a: f"Email '{a.get('subject')}' to {', '.join(map(str, a.get('to', [])))}"))
    reg.register(ToolSpec(
        name="draft_email",
        description="Open a pre-filled email draft in the user's mail app for them to review and send themselves "
                    "(no setup needed; use when send_email is not configured or the user wants to review).",
        input_schema={"type": "object", "properties": {
            "to": addr_list, "subject": {"type": "string", "maxLength": 300}, "body": {"type": "string", "maxLength": 1500}}},
        handler=draft, idempotent=False, purpose=lambda a: f"Draft email to {', '.join(map(str, a.get('to', [])))}"))
