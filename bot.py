import os
import imaplib
import email
from email.header import decode_header
import sqlite3
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
YOUR_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Bratislava"))
IMAP_SERVER = os.getenv("IMAP_SERVER")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_EMAIL = os.getenv("IMAP_EMAIL")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD")

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
conversation_history = []

def init_db():
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            remind_at DATETIME NOT NULL,
            done INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def add_reminder(text, remind_at):
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("INSERT INTO reminders (text, remind_at) VALUES (?, ?)", (text, remind_at))
    conn.commit()
    conn.close()

def get_pending_reminders():
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("SELECT id, text FROM reminders WHERE done=0 AND remind_at <= ? ORDER BY remind_at", (datetime.now(TZ),))
    rows = c.fetchall()
    conn.close()
    return rows

def get_todays_reminders():
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    c.execute("SELECT text, remind_at FROM reminders WHERE done=0 AND remind_at LIKE ?", (f"{today}%",))
    rows = c.fetchall()
    conn.close()
    return rows

def mark_done(reminder_id):
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("UPDATE reminders SET done=1 WHERE id=?", (reminder_id,))
    conn.commit()
    conn.close()

def parse_relative_datetime(day_str, time_str):
    now = datetime.now(TZ)
    hour, minute = map(int, time_str.split(":"))
    day_str = day_str.lower().strip()
    days_sk = {
        "pondelok": 0, "utorok": 1, "streda": 2, "stredy": 2,
        "štvrtok": 3, "stvrtok": 3, "piatok": 4, "sobota": 5, "nedeľa": 6, "nedela": 6
    }
    if day_str in ("dnes", "today"):
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    elif day_str in ("zajtra", "tomorrow"):
        target = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    elif day_str in ("pozajtra",):
        target = (now + timedelta(days=2)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    elif day_str in days_sk:
        target_weekday = days_sk[day_str]
        days_ahead = (target_weekday - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        target = (now + timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    else:
        return None
    return target

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
        date = msg["Date"]
        emails.append({"from": sender, "subject": subject, "date": date})
    mail.logout()
    return emails

async def check_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != YOUR_CHAT_ID:
        return
    unseen = "--new" in (context.args or [])
    count = 5
    for arg in (context.args or []):
        if arg.isdigit():
            count = int(arg)
    try:
        emails = fetch_emails(count=count, unseen_only=unseen)
    except Exception as e:
        await update.message.reply_text(f"Chyba pri pripájaní k emailu: {e}")
        return
    if not emails:
        await update.message.reply_text("Žiadne nové emaily." if unseen else "Žiadne emaily.")
        return
    msg = f"📧 *{'Neprečítané' if unseen else 'Posledné'} emaily:*\n\n"
    for e in emails:
        msg += f"*Od:* {e['from']}\n*Predmet:* {e['subject']}\n*Dátum:* {e['date']}\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def check_new_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != YOUR_CHAT_ID:
        return
    try:
        emails = fetch_emails(count=10, unseen_only=True)
    except Exception as e:
        await update.message.reply_text(f"Chyba pri pripájaní k emailu: {e}")
        return
    if not emails:
        await update.message.reply_text("Žiadne neprečítané emaily.")
        return
    msg = "📧 *Neprečítané emaily:*\n\n"
    for e in emails:
        msg += f"*Od:* {e['from']}\n*Predmet:* {e['subject']}\n*Dátum:* {e['date']}\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def ask_claude(user_message):
    now = datetime.now(TZ)
    days_sk = ["pondelok", "utorok", "streda", "štvrtok", "piatok", "sobota", "nedeľa"]
    day_name = days_sk[now.weekday()]
    message_with_date = f"[Dnes je {day_name} {now.strftime('%d.%m.%Y')}, {now.strftime('%H:%M')}]\n{user_message}"
    conversation_history.append({"role": "user", "content": message_with_date})
    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system="""Si osobný asistent Martina. Komunikuješ po slovensky, si stručný a praktický.

Ak chce Martin pridať pripomienku, odpovedz PRESNE takto a nič iné:
REMINDER|DEŇ|HH:MM|text

DEŇ môže byť LEN jedno z: dnes, zajtra, pozajtra, pondelok, utorok, streda, štvrtok, piatok, sobota, nedeľa
HH:MM je čas v 24h formáte

Príklady:
REMINDER|zajtra|08:30|Porada
REMINDER|pondelok|14:00|Odoslať faktúru

Ak nejde o pripomienku, odpovedaj normálne.""",
        messages=conversation_history
    )
    reply = response.content[0].text
    conversation_history.append({"role": "assistant", "content": reply})
    return reply

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != YOUR_CHAT_ID:
        return
    user_message = update.message.text
    await update.message.reply_text("⏳ Premýšľam...")
    reply = await ask_claude(user_message)
    if reply.startswith("REMINDER|"):
        parts = reply.split("|")
        day_str = parts[1].strip()
        time_str = parts[2].strip()
        text = parts[3].strip()
        remind_at = parse_relative_datetime(day_str, time_str)
        if remind_at:
            add_reminder(text, remind_at.strftime("%Y-%m-%d %H:%M"))
            await update.message.reply_text(f"✅ Pripomienka uložená: {text}\n📅 {remind_at.strftime('%d.%m.%Y o %H:%M')}")
        else:
            await update.message.reply_text(f"❌ Nepodarilo sa rozpoznať deň: {day_str}")
    else:
        await update.message.reply_text(reply)

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != YOUR_CHAT_ID:
        return
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("SELECT id, text, remind_at FROM reminders WHERE done=0 ORDER BY remind_at")
    rows = c.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Žiadne pripomienky.")
        return
    msg = "📋 *Tvoje pripomienky:*\n\n"
    for row in rows:
        msg += f"• {row[2]} – {row[1]} (id:{row[0]})\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def delete_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != YOUR_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text("Použitie: /delete 3")
        return
    mark_done(int(context.args[0]))
    await update.message.reply_text("✅ Pripomienka vymazaná.")

async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    reminders = get_pending_reminders()
    for r in reminders:
        await context.bot.send_message(chat_id=YOUR_CHAT_ID, text=f"🔔 Pripomienka: {r[1]}")
        mark_done(r[0])

async def morning_summary(context: ContextTypes.DEFAULT_TYPE):
    reminders = get_todays_reminders()
    if not reminders:
        msg = "🌅 Dobré ráno Martin! Dnes nemáš žiadne pripomienky."
    else:
        msg = "🌅 Dobré ráno Martin! Dnešné pripomienky:\n\n"
        for r in reminders:
            msg += f"• {r[1]} – {r[0]}\n"
    await context.bot.send_message(chat_id=YOUR_CHAT_ID, text=msg)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != YOUR_CHAT_ID:
        return
    msg = (
        "📖 *Príkazy:*\n\n"
        "/e — posledných 5 emailov\n"
        "/e 10 — posledných 10 emailov\n"
        "/en — neprečítané emaily\n"
        "/r — zoznam pripomienok\n"
        "/d 3 — zmazať pripomienku č. 3\n"
        "/h — táto nápoveda\n\n"
        "Alebo mi napíš čokoľvek 💬"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("h", help_command))
    app.add_handler(CommandHandler("reminders", list_reminders))
    app.add_handler(CommandHandler("r", list_reminders))
    app.add_handler(CommandHandler("delete", delete_reminder))
    app.add_handler(CommandHandler("d", delete_reminder))
    app.add_handler(CommandHandler("emails", check_emails))
    app.add_handler(CommandHandler("e", check_emails))
    app.add_handler(CommandHandler("en", check_new_emails))
    app.job_queue.run_repeating(check_reminders, interval=60, first=10)
    app.job_queue.run_daily(morning_summary, time=time(hour=8, minute=0, tzinfo=TZ))
    print("Bot beží...")
    app.run_polling()

if __name__ == "__main__":
    main()
