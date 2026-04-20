import re
from html import escape, unescape
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.ext import ContextTypes
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, YOUR_CHAT_ID, TZ
from db import (
    add_reminder, get_pending_reminders, get_todays_reminders, mark_done, parse_relative_datetime,
    is_email_notified, mark_email_notified,
    add_todo, get_todos, mark_todo_done, delete_todo, edit_todo,
)
from emails import fetch_emails, send_reply
from gitlab import search_projects, create_issue, list_my_issues

gemini_client = genai.Client(api_key=GEMINI_API_KEY)
conversation_history = []
email_cache = {}
gitlab_cache = {}
pending_reply = {}


def format_todo_list(todos):
    msg = "📝 <b>Tvoje úlohy:</b>\n\n"
    now = datetime.now(TZ)
    for t in todos:
        try:
            created = datetime.strptime(t[2], "%Y-%m-%d %H:%M")
            days = (now - created.replace(tzinfo=TZ)).days
        except Exception:
            days = 0
        if days > 10:
            icon = "🔴"
        elif days > 5:
            icon = "🟠"
        else:
            icon = "🟢"
        if t[3]:
            msg += f"{icon} <s>{escape(t[1])}</s> <i>({days}d, id:{t[0]})</i>\n"
        else:
            msg += f"{icon} {escape(t[1])} <i>({days}d, id:{t[0]})</i>\n"
    return msg


def format_email_list(emails, title, highlight_unseen=False):
    msg = f"📧 <b>{escape(title)}</b>\n\n"
    keyboard = []
    for i, e in enumerate(emails):
        prefix = "🔵 " if highlight_unseen and e.get("unseen") else ""
        msg += f"{prefix}<b>{i+1}.</b> <b>Od:</b> {escape(e['from'])}\n<b>Predmet:</b> {escape(e['subject'])}\n<b>Dátum:</b> {escape(e['date'])}\n\n"
        cache_key = f"em{i}_{id(emails)}"
        email_cache[cache_key] = {
            "body": e.get("body", ""),
            "from": e["from"],
            "from_addr": e.get("from_addr", ""),
            "subject": e["subject"],
            "date": e["date"],
            "message_id": e.get("message_id", ""),
            "in_reply_to": e.get("in_reply_to", ""),
            "references": e.get("references", ""),
        }
        label = f"{i+1}. {e['subject'][:30]}"
        keyboard.append([InlineKeyboardButton(label, callback_data=cache_key)])
    return msg, keyboard


async def ask_gemini(user_message):
    now = datetime.now(TZ)
    days_sk = ["pondelok", "utorok", "streda", "štvrtok", "piatok", "sobota", "nedeľa"]
    day_name = days_sk[now.weekday()]
    message_with_date = f"[Dnes je {day_name} {now.strftime('%d.%m.%Y')}, {now.strftime('%H:%M')}]\n{user_message}"
    conversation_history.append({"role": "user", "parts": [{"text": message_with_date}]})
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=conversation_history,
        config=types.GenerateContentConfig(
            max_output_tokens=1000,
            system_instruction="""Si Igor, osobný asistent Martina. Tvoje meno je Igor. Komunikuješ po slovensky, si stručný a praktický.

Ak chce Martin vykonať akciu, odpovedz PRESNE v danom formáte. Tvoja odpoveď musí začínať príslušným prefixom (REMINDER|, EMAIL|, GITLAB|, TODO|) a nesmie obsahovať nič iné.

Dostupné akcie:

1) REMINDER|DEŇ|HH:MM|text — pripomienka (DEŇ: dnes, zajtra, pozajtra, pondelok-nedeľa)
   REMINDER|LIST — zoznam pripomienok
   Príklady: REMINDER|zajtra|08:30|Porada   REMINDER|LIST

2) EMAIL|LIST — posledných 5 emailov
   EMAIL|LIST|N — posledných N emailov
   EMAIL|NEW — nové neprečítané emaily
   Príklady: EMAIL|LIST   EMAIL|LIST|10   EMAIL|NEW

3) GITLAB|CREATE|kľúčové_slovo_projektu|názov|popis|estimate — vytvor issue (popis a estimate voliteľné, estimate: 30m, 1h, 2h, 1d)
   GITLAB|ISSUES — moje otvorené issues
   Príklady: GITLAB|CREATE|digitalka|Opraviť login||2h   GITLAB|ISSUES

4) TODO|ADD|text — pridaj úlohu
   TODO|LIST — zobraz úlohy
   TODO|DONE|id — splň úlohu
   TODO|DELETE|id — vymaž úlohu
   TODO|EDIT|id|nový text — uprav úlohu
   Príklady: TODO|ADD|Opraviť faktúru   TODO|LIST   TODO|DONE|3

Ak nejde o žiadnu akciu, odpovedaj normálne.""",
        ),
    )
    reply = response.text
    conversation_history.append({"role": "model", "parts": [{"text": reply}]})
    del conversation_history[:-40]
    return reply


_TONE_INSTRUCTIONS = {
    "friendly": (
        "Tón: priateľský, prívetivý, ľudský — ale NIE podliezavý. "
        "Žiadne frázy typu 'ďakujem za email', 'teším sa na spoluprácu', 'veľmi si vážim'. "
        "Tykanie/vykanie prispôsob originálu. Krátke, osobné, priame vety."
    ),
    "professional": (
        "Tón: jednoduchý, vecný, profesionálny. "
        "Bez zbytočností, bez fráz typu 'ďakujem za Váš email', 'teším sa na spoluprácu'. "
        "Tykanie/vykanie prispôsob originálu. Krátke vety, len nevyhnutné informácie."
    ),
}


def generate_reply_draft(subject, from_label, body_clean, tone="friendly"):
    tone_instr = _TONE_INSTRUCTIONS.get(tone, _TONE_INSTRUCTIONS["friendly"])
    prompt = (
        "Napíš koncept odpovede na tento email po slovensky. "
        f"{tone_instr} "
        "NEPRIDÁVAJ podpis ani záverečný pozdrav ('S pozdravom', 'Pekný deň' atď.) — doplní sa automaticky. "
        "Výstupom je len samotný text odpovede, bez úvodného komentára.\n\n"
        f"Od: {from_label}\n"
        f"Predmet: {subject}\n\n"
        f"Obsah:\n{body_clean}"
    )
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[{"role": "user", "parts": [{"text": prompt}]}],
        config=types.GenerateContentConfig(max_output_tokens=2000),
    )
    return (response.text or "").strip()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != YOUR_CHAT_ID:
        return
    user_message = update.message.text
    if update.message.reply_to_message and YOUR_CHAT_ID in pending_reply:
        data = pending_reply.pop(YOUR_CHAT_ID)
        key = data["key"]
        cached = email_cache.get(key)
        if not cached:
            await update.message.reply_text("Email expiroval, skús znova /e")
            return
        try:
            send_reply(
                to_addr=cached["from_addr"],
                subject=cached["subject"],
                body=user_message,
                reply_to_msgid=cached.get("message_id", ""),
                references=cached.get("references", ""),
                original_from=cached["from"],
                original_date=cached["date"],
                original_body_clean=_clean_email_body(cached.get("body", "")),
            )
            await update.message.reply_text(f"✉️ Odoslané: {escape(cached['from_addr'])}", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Chyba pri odosielaní: {e}")
        return
    await update.message.reply_text("⏳ Premýšľam...")
    reply = await ask_gemini(user_message)
    print(f"Gemini reply: {reply[:200]}")
    for line in reply.strip().splitlines():
        line = line.strip()
        if line.startswith(("REMINDER|", "EMAIL|", "GITLAB|", "TODO|")):
            reply = line
            break
    if reply.startswith("REMINDER|"):
        parts = reply.split("|")
        action = parts[1].strip()
        if action == "LIST":
            import sqlite3
            conn = sqlite3.connect("assistant.db")
            c = conn.cursor()
            c.execute("SELECT id, text, remind_at FROM reminders WHERE done=0 ORDER BY remind_at")
            rows = c.fetchall()
            conn.close()
            if not rows:
                await update.message.reply_text("Žiadne pripomienky.")
            else:
                msg = "📋 <b>Tvoje pripomienky:</b>\n\n"
                for row in rows:
                    msg += f"• {escape(row[2])} – {escape(row[1])} (id:{row[0]})\n"
                await update.message.reply_text(msg, parse_mode="HTML")
            return
        day_str = action
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
                estimate = parts[5].strip() if len(parts) > 5 else ""
                projects = search_projects(keyword)
                if not projects:
                    await update.message.reply_text(f"Nenašiel som projekt pre '{keyword}'.")
                    return
                if len(projects) == 1:
                    project = projects[0]
                    issue = create_issue(project["id"], title, description, estimate)
                    msg = f"✅ Issue vytvorený:\n<b>Projekt:</b> {escape(project['name'])}\n<b>#{issue['id']}:</b> {escape(issue['title'])}\n"
                    if issue.get("estimate"):
                        msg += f"<b>Estimate:</b> {escape(issue['estimate'])}\n"
                    msg += f"<a href=\"{issue['url']}\">Otvoriť v GitLab</a>"
                    await update.message.reply_text(msg, parse_mode="HTML")
                else:
                    keyboard = []
                    for i, p in enumerate(projects):
                        cache_key = f"gl{i}_{id(projects)}"
                        gitlab_cache[cache_key] = {"p": p["id"], "t": title, "d": description, "e": estimate}
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
    elif reply.startswith("TODO|"):
        parts = reply.split("|")
        action = parts[1].strip()
        if action == "ADD":
            text = parts[2].strip()
            add_todo(text)
            await update.message.reply_text(f"✅ Úloha pridaná: {text}")
        elif action == "LIST":
            todos = get_todos(include_done=True)
            if not todos:
                await update.message.reply_text("Nemáš žiadne úlohy.")
            else:
                await update.message.reply_text(format_todo_list(todos), parse_mode="HTML")
        elif action == "DELETE":
            todo_id = int(parts[2].strip())
            delete_todo(todo_id)
            await update.message.reply_text("🗑️ Úloha vymazaná.")
        elif action == "DONE":
            todo_id = int(parts[2].strip())
            mark_todo_done(todo_id)
            await update.message.reply_text("✅ Úloha splnená.")
        elif action == "EDIT":
            todo_id = int(parts[2].strip())
            new_text = parts[3].strip()
            edit_todo(todo_id, new_text)
            await update.message.reply_text(f"✏️ Úloha {todo_id} upravená: {new_text}")
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


async def list_todos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != YOUR_CHAT_ID:
        return
    todos = get_todos(include_done=True)
    if not todos:
        await update.message.reply_text("Nemáš žiadne úlohy.")
        return
    await update.message.reply_text(format_todo_list(todos), parse_mode="HTML")


async def todo_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != YOUR_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text("Použitie: /td 3")
        return
    mark_todo_done(int(context.args[0]))
    await update.message.reply_text("✅ Úloha splnená.")


async def todo_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != YOUR_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text("Použitie: /tx 3")
        return
    delete_todo(int(context.args[0]))
    await update.message.reply_text("🗑️ Úloha vymazaná.")


async def todo_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != YOUR_CHAT_ID:
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Použitie: /te 3 Nový text úlohy")
        return
    todo_id = int(context.args[0])
    new_text = " ".join(context.args[1:])
    edit_todo(todo_id, new_text)
    await update.message.reply_text(f"✏️ Úloha {todo_id} upravená: {new_text}")


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
    msg = "🌅 Dobré ráno Martin!\n\n"
    reminders = get_todays_reminders()
    if reminders:
        msg += "<b>📅 Dnešné pripomienky:</b>\n"
        for r in reminders:
            msg += f"• {r[1]} – {r[0]}\n"
        msg += "\n"
    todos = get_todos()
    if todos:
        now = datetime.now(TZ)
        msg += "<b>📝 Otvorené úlohy:</b>\n"
        for t in todos:
            try:
                created = datetime.strptime(t[2], "%Y-%m-%d %H:%M")
                days = (now - created.replace(tzinfo=TZ)).days
            except Exception:
                days = 0
            if days > 10:
                icon = "🔴"
            elif days > 5:
                icon = "🟠"
            else:
                icon = "🟢"
            msg += f"{icon} {t[1]} <i>({days}d)</i>\n"
        msg += "\n"
    if not reminders and not todos:
        msg += "Dnes nemáš žiadne pripomienky ani úlohy."
    await context.bot.send_message(chat_id=YOUR_CHAT_ID, text=msg, parse_mode="HTML")


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
        "/t — zoznam úloh\n"
        "/td 3 — splniť úlohu č. 3\n"
        "/te 3 Nový text — upraviť úlohu č. 3\n"
        "/tx 3 — vymazať úlohu č. 3\n"
        "/h — táto nápoveda\n\n"
        "Alebo mi napíš čokoľvek 💬"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


_QUOTE_PATTERNS = [
    re.compile(r'\n\s*>', re.MULTILINE),
    re.compile(r'\n\s*On .+ wrote:', re.IGNORECASE),
    re.compile(r'\n\s*Dňa .+ (napísal|pís|wrote)', re.IGNORECASE),
    re.compile(r'\n\s*-----\s*Original Message\s*-----', re.IGNORECASE),
    re.compile(r'\n\s*-----\s*Pôvodná správa\s*-----', re.IGNORECASE),
    re.compile(r'\n\s*From:\s.+\n\s*(Sent|Date):', re.IGNORECASE),
    re.compile(r'\n\s*Od:\s.+\n\s*(Odoslané|Dátum):', re.IGNORECASE),
]


def _clean_email_body(body):
    if "<html" in body.lower() or "<body" in body.lower():
        body = re.sub(r'<style[^>]*>.*?</style>', '', body, flags=re.DOTALL)
        body = re.sub(r'<script[^>]*>.*?</script>', '', body, flags=re.DOTALL)
        body = re.sub(r'<br\s*/?>', '\n', body, flags=re.IGNORECASE)
        body = re.sub(r'</p>', '\n\n', body, flags=re.IGNORECASE)
        body = re.sub(r'</div>', '\n', body, flags=re.IGNORECASE)
        body = re.sub(r'</tr>', '\n', body, flags=re.IGNORECASE)
        body = re.sub(r'</li>', '\n', body, flags=re.IGNORECASE)
        body = re.sub(r'<[^>]+>', '', body)
        body = unescape(body)
        body = re.sub(r'[ \t]+', ' ', body)
        body = re.sub(r'\n ', '\n', body)
        body = re.sub(r'\n{3,}', '\n\n', body)
        body = body.strip()
    return body


def _split_latest_reply(body):
    earliest = len(body)
    for pattern in _QUOTE_PATTERNS:
        m = pattern.search(body)
        if m and m.start() < earliest:
            earliest = m.start()
    if earliest < len(body):
        return body[:earliest].rstrip(), body[earliest:].strip()
    return body, ""


def _truncate(body, limit=3500):
    if len(body) > limit:
        return body[:limit] + "\n\n... (skrátené)"
    return body


def _email_header(cached):
    return (
        f"📩 <b>{escape(cached['subject'])}</b>\n"
        f"<b>Od:</b> {escape(cached['from'])}\n"
        f"<b>Dátum:</b> {escape(cached['date'])}\n"
        f"{'─' * 20}\n\n"
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != YOUR_CHAT_ID:
        return
    await query.answer()
    raw = query.data
    if raw.startswith("mf_"):
        key = raw[3:]
        cached = email_cache.get(key)
        if cached is None:
            await context.bot.send_message(chat_id=YOUR_CHAT_ID, text="Email expiroval, skús znova /e")
            return
        body = _clean_email_body(cached["body"] or "")
        await context.bot.send_message(
            chat_id=YOUR_CHAT_ID,
            text=_email_header(cached) + escape(_truncate(body)),
            parse_mode="HTML",
        )
        return
    if raw.startswith("repsend_"):
        key = raw[8:]
        data = pending_reply.pop(YOUR_CHAT_ID, None)
        if not data or data.get("key") != key or not data.get("draft"):
            await query.edit_message_text("Návrh expiroval, skús znova.")
            return
        cached = email_cache.get(key)
        if not cached:
            await query.edit_message_text("Email expiroval.")
            return
        try:
            send_reply(
                to_addr=cached["from_addr"],
                subject=cached["subject"],
                body=data["draft"],
                reply_to_msgid=cached.get("message_id", ""),
                references=cached.get("references", ""),
                original_from=cached["from"],
                original_date=cached["date"],
                original_body_clean=_clean_email_body(cached.get("body", "")),
            )
            await query.edit_message_text(f"✉️ Odoslané: {escape(cached['from_addr'])}", parse_mode="HTML")
        except Exception as e:
            await query.edit_message_text(f"❌ Chyba pri odosielaní: {e}")
        return
    if raw.startswith("repedit_"):
        key = raw[8:]
        cached = email_cache.get(key)
        if not cached:
            await context.bot.send_message(chat_id=YOUR_CHAT_ID, text="Email expiroval.")
            return
        existing = pending_reply.get(YOUR_CHAT_ID) or {}
        draft = existing.get("draft", "")
        pending_reply[YOUR_CHAT_ID] = {"key": key}
        text = f"✏️ Napíš vlastnú odpoveď pre <b>{escape(cached['from_addr'])}</b>"
        if draft:
            text += f":\n\n<i>Pôvodný návrh:</i>\n<code>{escape(draft)}</code>"
        await context.bot.send_message(
            chat_id=YOUR_CHAT_ID,
            text=text,
            parse_mode="HTML",
            reply_markup=ForceReply(input_field_placeholder="Napíš odpoveď..."),
        )
        return
    if raw.startswith("repo_"):
        key = raw[5:]
        cached = email_cache.get(key)
        if cached is None:
            await context.bot.send_message(chat_id=YOUR_CHAT_ID, text="Email expiroval, skús znova /e")
            return
        if not cached.get("from_addr"):
            await context.bot.send_message(chat_id=YOUR_CHAT_ID, text="Nemám adresu odosielateľa.")
            return
        pending_reply[YOUR_CHAT_ID] = {"key": key}
        await context.bot.send_message(
            chat_id=YOUR_CHAT_ID,
            text=f"✏️ Napíš vlastnú odpoveď pre <b>{escape(cached['from_addr'])}</b>:",
            parse_mode="HTML",
            reply_markup=ForceReply(input_field_placeholder="Napíš odpoveď..."),
        )
        return
    if raw.startswith("repf_") or raw.startswith("repp_"):
        tone = "friendly" if raw.startswith("repf_") else "professional"
        key = raw[5:]
        cached = email_cache.get(key)
        if cached is None:
            await context.bot.send_message(chat_id=YOUR_CHAT_ID, text="Email expiroval, skús znova /e")
            return
        if not cached.get("from_addr"):
            await context.bot.send_message(chat_id=YOUR_CHAT_ID, text="Nemám adresu odosielateľa.")
            return
        tone_label = "priateľský" if tone == "friendly" else "profi"
        msg_obj = await context.bot.send_message(chat_id=YOUR_CHAT_ID, text=f"⏳ Pripravujem {tone_label} návrh...")
        try:
            clean_body = _clean_email_body(cached.get("body", ""))
            latest, _ = _split_latest_reply(clean_body)
            draft = generate_reply_draft(cached["subject"], cached["from"], latest or clean_body, tone=tone)
        except Exception as e:
            await msg_obj.edit_text(f"❌ Chyba Gemini: {e}")
            return
        if not draft:
            await msg_obj.edit_text("Gemini nevrátil žiadny návrh.")
            return
        pending_reply[YOUR_CHAT_ID] = {"key": key, "draft": draft}
        icon = "😊" if tone == "friendly" else "💼"
        text = (
            f"{icon} <b>Návrh ({tone_label})</b> pre {escape(cached['from_addr'])}\n\n"
            f"{escape(draft)}"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Odoslať", callback_data=f"repsend_{key}"),
            InlineKeyboardButton("✏️ Prepísať", callback_data=f"repedit_{key}"),
        ]])
        await msg_obj.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        return
    if raw.startswith("em"):
        cached = email_cache.get(raw)
        if cached is None:
            await context.bot.send_message(chat_id=YOUR_CHAT_ID, text="Email expiroval, skús znova /e")
            return
        body = cached["body"]
        if not body:
            await context.bot.send_message(chat_id=YOUR_CHAT_ID, text="Email nemá textový obsah.")
            return
        body = _clean_email_body(body)
        latest, rest = _split_latest_reply(body)
        buttons = []
        if rest:
            buttons.append([InlineKeyboardButton("📜 Zobraziť celé vlákno", callback_data=f"mf_{raw}")])
        if cached.get("from_addr"):
            buttons.append([
                InlineKeyboardButton("😊 Priateľsky", callback_data=f"repf_{raw}"),
                InlineKeyboardButton("💼 Profi", callback_data=f"repp_{raw}"),
                InlineKeyboardButton("✏️ Vlastná", callback_data=f"repo_{raw}"),
            ])
        keyboard = InlineKeyboardMarkup(buttons) if buttons else None
        await context.bot.send_message(
            chat_id=YOUR_CHAT_ID,
            text=_email_header(cached) + escape(_truncate(latest)),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return
    if raw.startswith("gl"):
        data = gitlab_cache.get(raw)
        if not data:
            await context.bot.send_message(chat_id=YOUR_CHAT_ID, text="Výber expiroval, skús znova.")
            return
        try:
            issue = create_issue(data["p"], data["t"], data.get("d", ""), data.get("e", ""))
            msg = f"✅ Issue vytvorený:\n<b>#{issue['id']}:</b> {escape(issue['title'])}\n"
            if issue.get("estimate"):
                msg += f"<b>Estimate:</b> {escape(issue['estimate'])}\n"
            msg += f"<a href=\"{issue['url']}\">Otvoriť v GitLab</a>"
            await query.edit_message_text(msg, parse_mode="HTML")
        except Exception as e:
            await query.edit_message_text(f"Chyba GitLab: {e}")
        return
