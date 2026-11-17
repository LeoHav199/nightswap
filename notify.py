"""Outgoing email and SMS.

Both functions return True when the message was actually delivered to a
provider, and False when no provider is configured (development), in which
case the message is printed to the console instead.
"""
import base64
import html
import json
import re
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage

from flask import current_app

import config


URL_LINE = re.compile(r"^https?://\S+$")
CODE_LINE = re.compile(r"(\b\d{6}\b)")


LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def plain_text(body):
    """The plain-text part of an email: the small layout marks (see email_html) taken out, [label](url) as "label: url"."""
    out = []
    for i, ln in enumerate(body.strip().split("\n")):
        if i == 0 and ln.startswith("^ "):
            continue
        if ln.startswith(("# ", "~ ")):
            ln = ln[2:]
        out.append(LINK.sub(lambda m: "%s: %s" % (m.group(1), m.group(2)), ln))
    return "\n".join(out).strip() + "\n"


def email_html(subject, body, buttons=None):
    """Branded HTML version of a plain-text email: wordmark, paragraphs, links as buttons.
    buttons: {url: label} for links that shouldn't say "Open Spare"; buttons after the first are outlined.
    Optional marks in the text (17 Nov 2026): a first line "^ ..." is the inbox preview text (hidden), "# ..." a
    headline, lines "- ..." a list, a paragraph "~ ..." small print, and "[label](url)" a text link."""
    parts = []
    shown = 0
    lines_all = body.strip().split("\n")
    preheader = ""
    if lines_all and lines_all[0].startswith("^ "):
        preheader = lines_all.pop(0)[2:].strip()

    def inline(text):
        text = html.escape(text)
        text = LINK.sub(lambda m: '<a href="%s" style="color:inherit">%s</a>' % (m.group(2), m.group(1)), text)
        return re.sub(r'(?<!href=")(https?://[^\s<"]+)', r'<a href="\1" style="color:#d4ff3a">\1</a>', text)

    def flush(lines):
        if not lines:
            return
        if all(ln.startswith("- ") for ln in lines):
            items = "".join('<li style="margin:0 0 6px">%s</li>' % inline(ln[2:]) for ln in lines)
            parts.append('<ul style="margin:0 0 18px;padding-left:20px">%s</ul>' % items)
            return
        if lines[0].startswith("# "):
            parts.append('<h1 style="margin:4px 0 14px;font-size:28px;line-height:1.15;font-weight:800;color:#f5f3ec">%s</h1>'
                         % inline(lines[0][2:]))
            lines = lines[1:]
            if not lines:
                return
        if lines[0].startswith("~ "):
            parts.append('<p style="margin:20px 0 0;font-size:12px;line-height:1.5;color:#928eb4">%s</p>'
                         % "<br>".join(inline(ln[2:] if ln.startswith("~ ") else ln) for ln in lines))
            return
        text = "<br>".join(inline(ln) for ln in lines)
        if len(lines) == 1 and len(text) < 40 and CODE_LINE.search(text):
            text = CODE_LINE.sub(r'<span style="font-size:32px;letter-spacing:6px;font-weight:700;color:#f5f3ec">\1</span>', text)
        parts.append('<p style="margin:0 0 16px">%s</p>' % text)

    for block in "\n".join(lines_all).split("\n\n"):
        lines = []
        for ln in (x.strip() for x in block.split("\n")):
            if not ln:
                continue
            if URL_LINE.match(ln):
                flush(lines)
                lines = []
                look = ("background:#d4ff3a;color:#15132b" if not shown
                        else "background:transparent;color:#d4ff3a;border:2px solid #d4ff3a")
                parts.append('<p style="margin:8px 0 24px"><a href="%s" style="display:inline-block;%s;'
                             'font-weight:700;text-decoration:none;padding:13px 22px;border-radius:999px">%s</a></p>'
                             % (html.escape(ln, quote=True), look, html.escape((buttons or {}).get(ln, "Open Spare"))))
                shown += 1
            else:
                lines.append(ln)
        flush(lines)
    hidden = ('<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent">%s</div>'
              % html.escape(preheader)) if preheader else ""
    return (
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>%s</title></head>'
        '<body style="margin:0;background:#15132b;padding:24px 12px;font-family:-apple-system,Segoe UI,'
        'Helvetica,Arial,sans-serif">%s'
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0"><tr><td align="center">'
        '<table role="presentation" width="100%%" style="max-width:520px;background:#1d1a37;border-radius:14px" cellpadding="0" cellspacing="0">'
        '<tr><td style="padding:22px 28px 6px;font-size:24px;font-weight:900;color:#f5f3ec;letter-spacing:-.5px">spare</td></tr>'
        '<tr><td style="padding:12px 28px 26px;color:#d6d4e4;font-size:15px;line-height:1.55">%s</td></tr>'
        '</table>'
        '<p style="color:#928eb4;font-size:12px;margin:16px 0 0">%s · <a href="mailto:%s" style="color:#928eb4">%s</a></p>'
        '</td></tr></table></body></html>'
    ) % (html.escape(subject), hidden, "".join(parts), html.escape(company_line()), config.SUPPORT_EMAIL, config.SUPPORT_EMAIL)


def company_line():
    """#20: who runs Spare, in every email once the KvK details are set."""
    if config.COMPANY_NAME and config.COMPANY_ADDRESS and config.KVK_NUMBER:
        return "%s · %s · KvK %s" % (config.COMPANY_NAME, config.COMPANY_ADDRESS, config.KVK_NUMBER)
    return "Spare · Rotterdam"


def send_email(to, subject, body, attachments=(), headers=None, buttons=None):
    """attachments: (filename, bytes) pairs, e.g. the nightly backup. headers: extra ones, e.g. List-Unsubscribe.
    buttons: {url: label} for the HTML version (see email_html)."""
    if not config.SMTP_HOST:
        current_app.logger.warning("EMAIL (not sent, no SMTP configured) to=%s subject=%s\n%s", to, subject, body)
        return False
    msg = EmailMessage()
    msg["From"] = config.MAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg["Reply-To"] = config.SUPPORT_EMAIL  # #17: replies reach hello@ (read in iCloud), whatever sends the mail
    for name, value in (headers or {}).items():
        msg[name] = value
    msg.set_content(plain_text(body))
    msg.add_alternative(email_html(subject, body, buttons), subtype="html")
    for filename, data in attachments:
        msg.add_attachment(data, maintype="application", subtype="octet-stream", filename=filename)
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as smtp:
            smtp.starttls()
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
        return True
    except Exception:
        current_app.logger.exception("Email to %s failed", to)
        return False


def send_sms(to, body):
    if not (config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN and config.TWILIO_FROM):
        current_app.logger.warning("SMS (not sent, no Twilio configured) to=%s\n%s", to, body)
        return False
    url = "https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json" % config.TWILIO_ACCOUNT_SID
    data = urllib.parse.urlencode({"To": to, "From": config.TWILIO_FROM, "Body": body}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    token = base64.b64encode(("%s:%s" % (config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)).encode()).decode()
    req.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            json.load(resp)
        return True
    except Exception:
        current_app.logger.exception("SMS to %s failed", to)
        return False


def notify_admins(subject, body):
    for email in config.ADMIN_EMAILS:
        send_email(email, subject, body)
