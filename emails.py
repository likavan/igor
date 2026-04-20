import imaplib
import email
import email.utils
import smtplib
import time
from email.header import decode_header
from email.message import EmailMessage
from config import (
    IMAP_SERVER, IMAP_PORT, IMAP_EMAIL, IMAP_PASSWORD, IMAP_SENT_FOLDER,
    SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, TZ,
)


def decode_mime_header(header):
    parts = decode_header(header or "")
    result = []
    for data, charset in parts:
        if isinstance(data, bytes):
            result.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(data)
    return "".join(result)


def extract_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                body = part.get_payload(decode=True).decode(charset, errors="replace")
                break
            elif ct == "text/html" and not body:
                charset = part.get_content_charset() or "utf-8"
                body = part.get_payload(decode=True).decode(charset, errors="replace")
    else:
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode(charset, errors="replace")
    return body


def fetch_emails(count=5, unseen_only=False):
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(IMAP_EMAIL, IMAP_PASSWORD)
    mail.select("INBOX")
    criterion = "UNSEEN" if unseen_only else "ALL"
    _, data = mail.search(None, criterion)
    ids = data[0].split()
    if not ids:
        mail.logout()
        return []
    latest_ids = ids[-count:]
    emails = []
    for eid in reversed(latest_ids):
        _, msg_data = mail.fetch(eid, "(BODY.PEEK[] FLAGS)")
        raw_flags = msg_data[0][0].decode() if msg_data[0][0] else ""
        unseen = "\\Seen" not in raw_flags
        msg = email.message_from_bytes(msg_data[0][1])
        sender = decode_mime_header(msg["From"])
        _, from_addr = email.utils.parseaddr(msg["From"] or "")
        subject = decode_mime_header(msg["Subject"])
        message_id = msg["Message-ID"] or f"{eid.decode()}-{msg['Date']}"
        in_reply_to = msg["In-Reply-To"] or ""
        references = msg["References"] or ""
        date_raw = msg["Date"]
        try:
            dt = email.utils.parsedate_to_datetime(date_raw).astimezone(TZ)
            date = dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            date = date_raw
        body = extract_body(msg)
        emails.append({
            "from": sender, "from_addr": from_addr,
            "subject": subject, "date": date,
            "message_id": message_id,
            "in_reply_to": in_reply_to, "references": references,
            "unseen": unseen, "body": body,
        })
    mail.logout()
    return emails


def send_reply(to_addr, subject, body, reply_to_msgid, references, original_from, original_date, original_body_clean):
    msg = EmailMessage()
    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    msg["From"] = SMTP_USERNAME
    msg["To"] = to_addr
    msg["Subject"] = reply_subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid()
    if reply_to_msgid:
        msg["In-Reply-To"] = reply_to_msgid
        chain = f"{references} {reply_to_msgid}".strip() if references else reply_to_msgid
        msg["References"] = chain
    signature = "S pozdravom,\nMartin Likavčan\n— \nmartin.likavcan@digitalka.sk\n+421908558871"
    quoted = "\n".join(f"> {line}" for line in (original_body_clean or "").splitlines())
    full = f"{body}\n\n{signature}\n\nDňa {original_date}, {original_from} napísal:\n{quoted}"
    msg.set_content(full)

    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=15) as s:
            s.login(SMTP_USERNAME, SMTP_PASSWORD)
            s.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(SMTP_USERNAME, SMTP_PASSWORD)
            s.send_message(msg)

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT, timeout=15)
        mail.login(IMAP_EMAIL, IMAP_PASSWORD)
        mail.append(IMAP_SENT_FOLDER, "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        mail.logout()
    except Exception as e:
        print(f"Warning: failed to append to Sent folder '{IMAP_SENT_FOLDER}': {e}")
