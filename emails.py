import imaplib
import email
import email.utils
from email.header import decode_header
from config import IMAP_SERVER, IMAP_PORT, IMAP_EMAIL, IMAP_PASSWORD, TZ


def decode_mime_header(header):
    parts = decode_header(header or "")
    result = []
    for data, charset in parts:
        if isinstance(data, bytes):
            result.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(data)
    return "".join(result)


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
        subject = decode_mime_header(msg["Subject"])
        message_id = msg["Message-ID"] or f"{eid.decode()}-{msg['Date']}"
        date_raw = msg["Date"]
        try:
            dt = email.utils.parsedate_to_datetime(date_raw).astimezone(TZ)
            date = dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            date = date_raw
        emails.append({"from": sender, "subject": subject, "date": date, "message_id": message_id, "unseen": unseen})
    mail.logout()
    return emails


def fetch_email_body(message_id):
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(IMAP_EMAIL, IMAP_PASSWORD)
    mail.select("INBOX")
    _, data = mail.search(None, f'HEADER "Message-ID" "{message_id}"')
    ids = data[0].split()
    if not ids:
        mail.logout()
        return None
    _, msg_data = mail.fetch(ids[0], "(BODY.PEEK[])")
    msg = email.message_from_bytes(msg_data[0][1])
    mail.logout()

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
        body = msg.get_payload(decode=True).decode(charset, errors="replace")

    return body
