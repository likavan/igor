from html import escape
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes
import anthropic
from config import ANTHROPIC_API_KEY, YOUR_CHAT_ID, TZ
from db import add_reminder, get_pending_reminders, get_todays_reminders, mark_done, parse_relative_datetime, is_email_notified, mark_email_notified
from emails import fetch_emails

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
conversation_history = []


def format_email_list(emails, title, highlight_unseen=False):
    msg = f"📧 <b>{escape(title)}</b>\n\n"
    for e in emails:
        prefix = "🔵 " if highlight_unseen and e.get("unseen") else ""
        msg += f"{prefix}<b>Od:</b> {escape(e['from'])}\n<b>Predmet:</b> {escape(e['subject'])}\n<b>Dátum:</b> {escape(e['date'])}\n\n"
    return msg


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
    import sqlite3
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("SELECT id, text, remind_at FROM reminders WHERE done=0 ORDER BY remind_at")
    rows = c.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Žiadne pripomienky.")
        return
    msg = "📋 <b>Tvoje pripomienky:</b>\n\n"
    for row in rows:
        msg += f"• {escape(row[2])} – {escape(row[1])} (id:{row[0]})\n"
    await update.message.reply_text(msg, parse_mode="HTML")


async def delete_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != YOUR_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text("Použitie: /delete 3")
        return
    mark_done(int(context.args[0]))
    await update.message.reply_text("✅ Pripomienka vymazaná.")


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
    title = "Neprečítané emaily:" if unseen else "Posledné emaily:"
    msg = format_email_list(emails, title, highlight_unseen=not unseen)
    await update.message.reply_text(msg, parse_mode="HTML")


async def check_new_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != YOUR_CHAT_ID:
        return
    try:
        emails = fetch_emails(count=10, unseen_only=True)
    except Exception as e:
        await update.message.reply_text(f"Chyba pri pripájaní k emailu: {e}")
        return
    new_emails = [e for e in emails if not is_email_notified(e["message_id"])]
    if not new_emails:
        await update.message.reply_text("Žiadne nové neprečítané emaily.")
        return
    for e in new_emails:
        mark_email_notified(e["message_id"])
    msg = format_email_list(new_emails, f"Nových emailov: {len(new_emails)}")
    await update.message.reply_text(msg, parse_mode="HTML")


async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    reminders = get_pending_reminders()
    for r in reminders:
        await context.bot.send_message(chat_id=YOUR_CHAT_ID, text=f"🔔 Pripomienka: {r[1]}")
        mark_done(r[0])


async def check_emails_periodic(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TZ)
    if now.weekday() >= 5:
        return
    if now.hour < 15 or (now.hour == 15 and now.minute < 30) or now.hour >= 22:
        return
    try:
        emails = fetch_emails(count=10, unseen_only=True)
    except Exception:
        return
    new_emails = [e for e in emails if not is_email_notified(e["message_id"])]
    if not new_emails:
        return
    for e in new_emails:
        mark_email_notified(e["message_id"])
    msg = format_email_list(new_emails, f"Máš {len(new_emails)} nových emailov:")
    await context.bot.send_message(chat_id=YOUR_CHAT_ID, text=msg, parse_mode="HTML")


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
        "📖 <b>Príkazy:</b>\n\n"
        "/e — posledných 5 emailov\n"
        "/e 10 — posledných 10 emailov\n"
        "/en — neprečítané emaily\n"
        "/r — zoznam pripomienok\n"
        "/d 3 — zmazať pripomienku č. 3\n"
        "/h — táto nápoveda\n\n"
        "Alebo mi napíš čokoľvek 💬"
    )
    await update.message.reply_text(msg, parse_mode="HTML")
