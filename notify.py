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


def email_html(subject, body):
    """Branded HTML version of a plain-text email: logo, paragraphs, links as buttons."""
    parts = []

    def flush(lines):
        if not lines:
            return
        text = "<br>".join(html.escape(ln) for ln in lines)
        text = re.sub(r"(https?://[^\s<]+)", r'<a href="\1" style="color:#d4ff3a">\1</a>', text)
        if len(lines) == 1 and len(text) < 40 and CODE_LINE.search(text):
            text = CODE_LINE.sub(r'<span style="font-size:32px;letter-spacing:6px;font-weight:700;color:#f5f3ec">\1</span>', text)
        parts.append('<p style="margin:0 0 16px">%s</p>' % text)

    for block in body.strip().split("\n\n"):
        lines = []
        for ln in (x.strip() for x in block.split("\n")):
            if not ln:
                continue
            if URL_LINE.match(ln):
                flush(lines)
                lines = []
                parts.append('<p style="margin:8px 0 24px"><a href="%s" style="display:inline-block;background:#d4ff3a;color:#15132b;'
                             'font-weight:700;text-decoration:none;padding:13px 22px;border-radius:999px">Open Spare</a></p>'
                             % html.escape(ln, quote=True))
            else:
                lines.append(ln)
        flush(lines)
    return (
        '<!doctype html><html><body style="margin:0;background:#15132b;padding:24px 12px;font-family:-apple-system,Segoe UI,'
        'Helvetica,Arial,sans-serif">'
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0"><tr><td align="center">'
        '<table role="presentation" width="100%%" style="max-width:520px;background:#1d1a37;border-radius:14px" cellpadding="0" cellspacing="0">'
        '<tr><td style="padding:22px 28px 6px;font-size:26px;font-weight:900;color:#f5f3ec;letter-spacing:.5px">'
        '%s spare</td></tr>'
        '<tr><td style="padding:12px 28px 24px;color:#d6d4e4;font-size:15px;line-height:1.55">%s</td></tr>'
        '</table>'
        '<p style="color:#77739a;font-size:12px;margin:16px 0 0">Spare · Rotterdam · <a href="mailto:%s" style="color:#77739a">%s</a></p>'
        '</td></tr></table></body></html>'
    ) % (_logo_img(), "".join(parts), config.SUPPORT_EMAIL, config.SUPPORT_EMAIL)


def _logo_img():
    # Email apps don't show inline SVG, so use the PNG from the live site (none in local development).
    if not config.BASE_URL:
        return ""
    return ('<img src="%s/static/wallet/logo@2x.png" width="30" height="30" alt="" '
            'style="vertical-align:-6px;margin-right:4px;border:0">' % html.escape(config.BASE_URL.rstrip("/"), quote=True))


def send_email(to, subject, body):
    if not config.SMTP_HOST:
        current_app.logger.warning("EMAIL (not sent, no SMTP configured) to=%s subject=%s\n%s", to, subject, body)
        return False
    msg = EmailMessage()
    msg["From"] = config.MAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_alternative(email_html(subject, body), subtype="html")
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
