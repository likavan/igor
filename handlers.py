import json
from html import escape
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
import anthropic
from config import ANTHROPIC_API_KEY, YOUR_CHAT_ID, TZ
from db import add_reminder, get_pending_reminders, get_todays_reminders, mark_done, parse_relative_datetime, is_email_notified, mark_email_notified
from emails import fetch_emails, fetch_email_body
from gitlab import search_projects, create_issue, list_my_issues

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
conversation_history = []
email_cache = {}
gitlab_cache = {}


def format_email_list(emails, title, highlight_unseen=False):
    msg = f"📧 <b>{escape(title)}</b>\n\n"
    keyboard = []
    for i, e in enumerate(emails):
        prefix = "🔵 " if highlight_unseen and e.get("unseen") else ""
        msg += f"{prefix}<b>{i+1}.</b> <b>Od:</b> {escape(e['from'])}\n<b>Predmet:</b> {escape(e['subject'])}\n<b>Dátum:</b> {escape(e['date'])}\n\n"
        cache_key = f"em{i}_{id(emails)}"
        email_cache[cache_key] = e["message_id"]
        label = f"{i+1}. {e['subject'][:30]}"
        keyboard.append([InlineKeyboardButton(label, callback_data=cache_key)])
    return msg, keyboard


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

Ak chce Martin vykonať akciu, odpovedz PRESNE v danom formáte a nič iné:

PRIPOMIENKA:
REMINDER|DEŇ|HH:MM|text
DEŇ môže byť LEN: dnes, zajtra, pozajtra, pondelok, utorok, streda, štvrtok, piatok, sobota, nedeľa
Príklady:
REMINDER|zajtra|08:30|Porada
REMINDER|pondelok|14:00|Odoslať faktúru

EMAILY:
EMAIL|AKCIA
AKCIA môže byť:
- LIST — zobraz posledných 5 emailov
- LIST|N — zobraz posledných N emailov
- NEW — zobraz nové neprečítané emaily
Príklady:
EMAIL|LIST (keď sa pýta na emaily, poštu, maily)
EMAIL|LIST|10 (keď chce viac emailov)
EMAIL|NEW (keď sa pýta na nové/neprečítané emaily)

GITLAB:
GITLAB|AKCIA|parametre
Akcie:
- GITLAB|CREATE|kľúčové_slovo_projektu|názov_tasku|popis (popis je voliteľný)
- GITLAB|ISSUES — zobraz moje otvorené issues
Príklady:
GITLAB|CREATE|digitalka|Opraviť login stránku|Nefunguje prihlásenie cez SSO
GITLAB|CREATE|eshop|Pridať export objednávok
GITLAB|ISSUES

Ak nejde o žiadnu akciu, odpovedaj normálne.""",
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
    elif reply.startswith("EMAIL|"):
        parts = reply.split("|")
        action = parts[1].strip()
        try:
            if action == "NEW":
                emails = fetch_emails(count=10, unseen_only=True)
                new_emails = [e for e in emails if not is_email_notified(e["message_id"])]
                if not new_emails:
                    await update.message.reply_text("Žiadne nové neprečítané emaily.")
                    return
                for e in new_emails:
                    mark_email_notified(e["message_id"])
                msg, keyboard = format_email_list(new_emails, f"Nových emailov: {len(new_emails)}")
            else:
                count = int(parts[2]) if len(parts) > 2 else 5
                emails = fetch_emails(count=count, unseen_only=False)
                if not emails:
                    await update.message.reply_text("Žiadne emaily.")
                    return
                msg, keyboard = format_email_list(emails, "Posledné emaily:", highlight_unseen=True)
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            await update.message.reply_text(f"Chyba pri pripájaní k emailu: {e}")
    elif reply.startswith("GITLAB|"):
        parts = reply.split("|")
        action = parts[1].strip()
        try:
            if action == "CREATE":
                keyword = parts[2].strip()
                title = parts[3].strip()
                description = parts[4].strip() if len(parts) > 4 else ""
                projects = search_projects(keyword)
                if not projects:
                    await update.message.reply_text(f"Nenašiel som projekt pre '{keyword}'.")
                    return
                if len(projects) == 1:
                    project = projects[0]
                    issue = create_issue(project["id"], title, description)
                    msg = (
                        f"✅ Issue vytvorený:\n"
                        f"<b>Projekt:</b> {escape(project['name'])}\n"
                        f"<b>#{issue['id']}:</b> {escape(issue['title'])}\n"
                        f"<a href=\"{issue['url']}\">Otvoriť v GitLab</a>"
                    )
                    await update.message.reply_text(msg, parse_mode="HTML")
                else:
                    keyboard = []
                    for i, p in enumerate(projects):
                        cache_key = f"gl{i}_{id(projects)}"
                        gitlab_cache[cache_key] = {"p": p["id"], "t": title, "d": description}
                        keyboard.append([InlineKeyboardButton(p["name"], callback_data=cache_key)])
                    await update.message.reply_text(
                        f"Našiel som viac projektov pre '<b>{escape(keyword)}</b>'.\nVyber projekt:",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
            elif action == "ISSUES":
                issues = list_my_issues()
                if not issues:
                    await update.message.reply_text("Nemáš žiadne otvorené issues.")
                    return
                msg = "📋 <b>Moje otvorené issues:</b>\n\n"
                for i in issues:
                    msg += f"• <b>#{i['id']}</b> {escape(i['title'])}\n  <i>{escape(i['project'])}</i>\n\n"
                await update.message.reply_text(msg, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"Chyba GitLab: {e}")
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
    msg, keyboard = format_email_list(emails, title, highlight_unseen=not unseen)
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


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
    msg, keyboard = format_email_list(new_emails, f"Nových emailov: {len(new_emails)}")
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


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
    msg, keyboard = format_email_list(new_emails, f"Máš {len(new_emails)} nových emailov:")
    await context.bot.send_message(chat_id=YOUR_CHAT_ID, text=msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


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


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != YOUR_CHAT_ID:
        return
    await query.answer()
    raw = query.data
    if raw.startswith("em"):
        message_id = email_cache.get(raw)
        if not message_id:
            await context.bot.send_message(chat_id=YOUR_CHAT_ID, text="Email expiroval, skús znova /e")
            return
        try:
            body = fetch_email_body(message_id)
            if not body:
                await context.bot.send_message(chat_id=YOUR_CHAT_ID, text="Email sa nepodarilo načítať.")
                return
            import re
            if "<html" in body.lower() or "<body" in body.lower():
                body = re.sub(r'<style[^>]*>.*?</style>', '', body, flags=re.DOTALL)
                body = re.sub(r'<[^>]+>', '', body)
                body = re.sub(r'\s+', ' ', body).strip()
            if len(body) > 3500:
                body = body[:3500] + "\n\n... (skrátené)"
            await context.bot.send_message(
                chat_id=YOUR_CHAT_ID,
                text=f"📩 <b>Email:</b>\n\n{escape(body)}",
                parse_mode="HTML",
            )
        except Exception as e:
            await context.bot.send_message(chat_id=YOUR_CHAT_ID, text=f"Chyba pri čítaní emailu: {e}")
        return
    if raw.startswith("gl"):
        data = gitlab_cache.get(raw)
        if not data:
            await context.bot.send_message(chat_id=YOUR_CHAT_ID, text="Výber expiroval, skús znova.")
            return
        try:
            issue = create_issue(data["p"], data["t"], data.get("d", ""))
            msg = (
                f"✅ Issue vytvorený:\n"
                f"<b>#{issue['id']}:</b> {escape(issue['title'])}\n"
                f"<a href=\"{issue['url']}\">Otvoriť v GitLab</a>"
            )
            await query.edit_message_text(msg, parse_mode="HTML")
        except Exception as e:
            await query.edit_message_text(f"Chyba GitLab: {e}")
