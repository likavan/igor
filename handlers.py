import json
from html import escape, unescape
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.ext import ContextTypes, CallbackQueryHandler
import anthropic
from config import ANTHROPIC_API_KEY, YOUR_CHAT_ID, TZ
from db import (
    add_reminder, get_pending_reminders, get_todays_reminders, mark_done, parse_relative_datetime,
    is_email_notified, mark_email_notified,
    add_todo, get_todos, mark_todo_done, delete_todo, edit_todo,
    create_project, get_projects, delete_project, add_subtask, get_subtasks, get_subtask,
    mark_subtask_done, edit_subtask_notes, delete_subtask, get_project_by_id,
)
from emails import fetch_emails
from gitlab import search_projects, create_issue, list_my_issues

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
conversation_history = []
email_cache = {}
gitlab_cache = {}
pending_edit = {}


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


def format_project_detail(project, subtasks):
    msg = f"📂 <b>{escape(project[1])}</b>\n\n"
    if not subtasks:
        msg += "<i>Žiadne podúlohy.</i>"
        return msg, []
    keyboard = []
    for s in subtasks:
        icon = "✅" if s[2] else "⬜"
        msg += f"{icon} {escape(s[1])}"
        if s[3]:
            msg += f"\n   📝 <i>{escape(s[3])}</i>"
        msg += f" <i>(id:{s[0]})</i>\n"
        if not s[2]:
            keyboard.append([
                InlineKeyboardButton("✅ Hotovo", callback_data=f"pt_done_{s[0]}"),
                InlineKeyboardButton("✏️ Poznámka", callback_data=f"pt_edit_{s[0]}"),
                InlineKeyboardButton("🗑️ Vymazať", callback_data=f"pt_del_{s[0]}"),
            ])
    return msg, keyboard


def format_email_list(emails, title, highlight_unseen=False):
    msg = f"📧 <b>{escape(title)}</b>\n\n"
    keyboard = []
    for i, e in enumerate(emails):
        prefix = "🔵 " if highlight_unseen and e.get("unseen") else ""
        msg += f"{prefix}<b>{i+1}.</b> <b>Od:</b> {escape(e['from'])}\n<b>Predmet:</b> {escape(e['subject'])}\n<b>Dátum:</b> {escape(e['date'])}\n\n"
        cache_key = f"em{i}_{id(emails)}"
        email_cache[cache_key] = {"body": e.get("body", ""), "from": e["from"], "subject": e["subject"], "date": e["date"]}
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
        system="""Si Igor, osobný asistent Martina. Tvoje meno je Igor. Komunikuješ po slovensky, si stručný a praktický.

Ak chce Martin vykonať akciu, odpovedz PRESNE v danom formáte. Tvoja odpoveď musí začínať príslušným prefixom (REMINDER|, EMAIL|, GITLAB|, TODO|, PROJECT|) a nesmie obsahovať nič iné.

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

5) PROJECT|CREATE|názov — vytvor projekt
   PROJECT|ADD|id_projektu|názov podúlohy|poznámka — pridaj podúlohu (poznámka voliteľná)
   PROJECT|LIST — zobraz projekty
   PROJECT|SHOW|id_projektu — detail projektu
   PROJECT|DELETE|id_projektu — vymaž projekt
   Príklady: PROJECT|CREATE|Redizajn webu   PROJECT|ADD|1|Návrh wireframe|Použiť Figmu   PROJECT|SHOW|1

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
    if update.message.reply_to_message and YOUR_CHAT_ID in pending_edit:
        subtask_id = pending_edit.pop(YOUR_CHAT_ID)
        edit_subtask_notes(subtask_id, user_message)
        subtask = get_subtask(subtask_id)
        if subtask:
            project = get_project_by_id(subtask[1])
            subtasks = get_subtasks(subtask[1])
            msg, keyboard = format_project_detail(project, subtasks)
            markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=markup)
        else:
            await update.message.reply_text("✏️ Poznámka uložená.")
        return
    await update.message.reply_text("⏳ Premýšľam...")
    reply = await ask_claude(user_message)
    for line in reply.strip().splitlines():
        line = line.strip()
        if line.startswith(("REMINDER|", "EMAIL|", "GITLAB|", "TODO|", "PROJECT|")):
            reply = line
            break
    is_action = reply.startswith(("REMINDER|", "EMAIL|", "GITLAB|", "TODO|", "PROJECT|"))
    if is_action:
        conversation_history.clear()
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
    elif reply.startswith("PROJECT|"):
        parts = reply.split("|")
        action = parts[1].strip()
        if action == "CREATE":
            name = parts[2].strip()
            project_id = create_project(name)
            await update.message.reply_text(f"📂 Projekt vytvorený: <b>{escape(name)}</b> (id:{project_id})", parse_mode="HTML")
        elif action == "ADD":
            project_id = int(parts[2].strip())
            text = parts[3].strip()
            notes = parts[4].strip() if len(parts) > 4 else ""
            subtask_id = add_subtask(project_id, text, notes)
            project = get_project_by_id(project_id)
            pname = escape(project[1]) if project else f"#{project_id}"
            await update.message.reply_text(f"✅ Podúloha pridaná do <b>{pname}</b>: {escape(text)} (id:{subtask_id})", parse_mode="HTML")
        elif action == "LIST":
            projects = get_projects()
            if not projects:
                await update.message.reply_text("Nemáš žiadne projekty.")
            else:
                msg = "📂 <b>Tvoje projekty:</b>\n\n"
                for p in projects:
                    subtasks = get_subtasks(p[0])
                    done = sum(1 for s in subtasks if s[2])
                    total = len(subtasks)
                    msg += f"• <b>{escape(p[1])}</b> ({done}/{total}) <i>(id:{p[0]})</i>\n"
                await update.message.reply_text(msg, parse_mode="HTML")
        elif action == "SHOW":
            project_id = int(parts[2].strip())
            project = get_project_by_id(project_id)
            if not project:
                await update.message.reply_text("Projekt neexistuje.")
            else:
                subtasks = get_subtasks(project_id)
                msg, keyboard = format_project_detail(project, subtasks)
                markup = InlineKeyboardMarkup(keyboard) if keyboard else None
                await update.message.reply_text(msg, parse_mode="HTML", reply_markup=markup)
        elif action == "DELETE":
            project_id = int(parts[2].strip())
            delete_project(project_id)
            await update.message.reply_text("🗑️ Projekt vymazaný.")
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


async def list_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != YOUR_CHAT_ID:
        return
    if context.args:
        project_id = int(context.args[0])
        project = get_project_by_id(project_id)
        if not project:
            await update.message.reply_text("Projekt neexistuje.")
            return
        subtasks = get_subtasks(project_id)
        msg, keyboard = format_project_detail(project, subtasks)
        markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=markup)
        return
    projects = get_projects()
    if not projects:
        await update.message.reply_text("Nemáš žiadne projekty.")
        return
    msg = "📂 <b>Tvoje projekty:</b>\n\n"
    for p in projects:
        subtasks = get_subtasks(p[0])
        done = sum(1 for s in subtasks if s[2])
        total = len(subtasks)
        msg += f"• <b>{escape(p[1])}</b> ({done}/{total}) <i>(id:{p[0]})</i>\n"
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
    msg = "🌅 Dobré ráno Martin!\n\n"
    reminders = get_todays_reminders()
    if reminders:
        msg += "<b>📅 Dnešné pripomienky:</b>\n"
        for r in reminders:
            msg += f"• {r[1]} – {r[0]}\n"
        msg += "\n"
    todos = get_todos()
    if todos:
        msg += "<b>📝 Otvorené úlohy:</b>\n"
        for t in todos:
            msg += f"• {t[1]}\n"
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
        "/p — zoznam projektov\n"
        "/p 1 — detail projektu č. 1\n"
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
        cached = email_cache.get(raw)
        if cached is None:
            await context.bot.send_message(chat_id=YOUR_CHAT_ID, text="Email expiroval, skús znova /e")
            return
        body = cached["body"]
        if not body:
            await context.bot.send_message(chat_id=YOUR_CHAT_ID, text="Email nemá textový obsah.")
            return
        import re
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
        if len(body) > 3500:
            body = body[:3500] + "\n\n... (skrátené)"
        header = (
            f"📩 <b>{escape(cached['subject'])}</b>\n"
            f"<b>Od:</b> {escape(cached['from'])}\n"
            f"<b>Dátum:</b> {escape(cached['date'])}\n"
            f"{'─' * 20}\n\n"
        )
        await context.bot.send_message(
            chat_id=YOUR_CHAT_ID,
            text=header + escape(body),
            parse_mode="HTML",
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
    if raw.startswith("pt_done_"):
        subtask_id = int(raw.split("_")[-1])
        mark_subtask_done(subtask_id)
        subtask = get_subtask(subtask_id)
        if subtask:
            project = get_project_by_id(subtask[1])
            subtasks = get_subtasks(subtask[1])
            msg, keyboard = format_project_detail(project, subtasks)
            markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            await query.edit_message_text(msg, parse_mode="HTML", reply_markup=markup)
        else:
            await query.edit_message_text("✅ Podúloha splnená.")
    elif raw.startswith("pt_del_"):
        subtask_id = int(raw.split("_")[-1])
        subtask = get_subtask(subtask_id)
        if subtask:
            project_id = subtask[1]
            delete_subtask(subtask_id)
            project = get_project_by_id(project_id)
            subtasks = get_subtasks(project_id)
            msg, keyboard = format_project_detail(project, subtasks)
            markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            await query.edit_message_text(msg, parse_mode="HTML", reply_markup=markup)
        else:
            await query.edit_message_text("🗑️ Podúloha vymazaná.")
    elif raw.startswith("pt_edit_"):
        subtask_id = int(raw.split("_")[-1])
        subtask = get_subtask(subtask_id)
        if not subtask:
            await context.bot.send_message(chat_id=YOUR_CHAT_ID, text="Podúloha neexistuje.")
            return
        pending_edit[YOUR_CHAT_ID] = subtask_id
        current_notes = subtask[4] or ""
        await context.bot.send_message(
            chat_id=YOUR_CHAT_ID,
            text=f"✏️ Uprav poznámku pre: <b>{escape(subtask[2])}</b>",
            parse_mode="HTML",
            reply_markup=ForceReply(input_field_placeholder=current_notes if current_notes else "Poznámka..."),
        )
