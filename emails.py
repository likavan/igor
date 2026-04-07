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
        _, msg_data = mail.fetch(eid, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])
        sender = decode_mime_header(msg["From"])
        subject = decode_mime_header(msg["Subject"])
        date_raw = msg["Date"]
        try:
            dt = email.utils.parsedate_to_datetime(date_raw).astimezone(TZ)
            date = dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            date = date_raw
        emails.append({"from": sender, "subject": subject, "date": date})
    mail.logout()
    return emails
