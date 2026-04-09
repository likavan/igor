import sqlite3
from datetime import datetime, timedelta
from config import TZ


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
    c.execute("""
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            done INTEGER DEFAULT 0,
            created_at DATETIME NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS notified_emails (
            message_id TEXT PRIMARY KEY,
            notified_at DATETIME NOT NULL
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


def add_todo(text):
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("INSERT INTO todos (text, created_at) VALUES (?, ?)", (text, datetime.now(TZ).strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()


def get_todos():
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("SELECT id, text, created_at FROM todos WHERE done=0 ORDER BY created_at")
    rows = c.fetchall()
    conn.close()
    return rows


def mark_todo_done(todo_id):
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("UPDATE todos SET done=1 WHERE id=?", (todo_id,))
    conn.commit()
    conn.close()


def edit_todo(todo_id, new_text):
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("UPDATE todos SET text=? WHERE id=?", (new_text, todo_id))
    conn.commit()
    conn.close()


def is_email_notified(message_id):
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("SELECT 1 FROM notified_emails WHERE message_id=?", (message_id,))
    result = c.fetchone()
    conn.close()
    return result is not None


def mark_email_notified(message_id):
    conn = sqlite3.connect("assistant.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO notified_emails (message_id, notified_at) VALUES (?, ?)",
              (message_id, datetime.now(TZ).strftime("%Y-%m-%d %H:%M")))
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
