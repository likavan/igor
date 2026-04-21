# Igor

**I**nteligentný  
**G**ranulárny  
**O**sobný  
**R**obot

Personal AI assistant running as a Telegram bot, powered by Gemini. Built to manage daily tasks and emails from a single chat interface.

## Features

### Gemini-powered chat
Natural language conversations in Slovak. Ask anything or trigger actions by just describing what you need.

### Reminders
- Create reminders in natural language ("pripomen mi zajtra o 8:30 porada")
- Supports Slovak day names and relative dates (dnes, zajtra, pozajtra)
- Automatic check every 60 seconds for due reminders
- Daily morning summary at 8:00

### Email (IMAP + SMTP)
- Check latest emails without marking them as read (IMAP PEEK)
- Unread emails highlighted with blue dot
- Read full email content via inline buttons
- Automatic hourly check for new emails (workdays 15:30-22:00)
- Tracks notified emails to avoid duplicate notifications
- Thread quoting stripped by default; **📜 Zobraziť celé vlákno** button to expand
- **Reply via SMTP** with three tone options:
  - **😊 Priateľsky** — Gemini drafts a warm, human reply
  - **💼 Profi** — Gemini drafts a concise, professional reply
  - **✏️ Vlastná** — write the reply yourself
- Drafts can be sent directly or rewritten. Signature auto-appended, original quoted, threading headers (`In-Reply-To`, `References`) set, message APPENDed to Sent folder

### Todo list
- Persistent tasks that stay until marked as done
- Create, list and complete tasks via natural language or commands
- Open tasks included in daily morning summary

### GitLab integration
- Create issues via natural language ("vytvor task v digitalka - opravit login")
- Smart project search by keyword with inline keyboard selection
- List your open issues
- New issues are auto-assigned to you

## Commands

| Command | Description |
|---------|-------------|
| `/e` | Last 5 emails |
| `/e N` | Last N emails |
| `/en` | New unread emails (marks as notified) |
| `/r` | List reminders |
| `/d N` | Delete reminder by ID |
| `/t` | List todo tasks |
| `/td N` | Complete todo by ID |
| `/te N text` | Edit todo text |
| `/tx N` | Delete todo by ID |
| `/h` | Help |

Or just write in natural language - Gemini handles the rest.

## Project structure

```
bot.py          # Entry point, registers handlers and jobs
config.py       # Environment variables and constants
db.py           # SQLite operations (reminders, todos, notified emails)
emails.py       # IMAP fetching + SMTP reply sending
gitlab.py       # GitLab API integration
handlers.py     # Telegram message and callback handlers, Gemini prompts
deploy.sh       # Git pull + pip install + service restart
```

## Setup

### 1. Clone and install

```bash
git clone git@github.com:likavan/igor.git ~/assistant
cd ~/assistant
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your values
```

See `.env.example` for all required variables.

### 3. Systemd service

Create `/etc/systemd/system/assistant.service`:

```ini
[Unit]
Description=Igor AI Assistant
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/assistant
ExecStart=/root/assistant/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable assistant
systemctl start assistant
```

### 4. Deploy

```bash
bash deploy.sh
```

Pulls latest code from GitHub, installs dependencies and restarts the service.

## Stack

- **Python** + python-telegram-bot
- **Gemini** (gemini-2.5-flash, free tier) via google-genai SDK
- **SQLite** for reminders, todos, and email notification tracking
- **IMAP + SMTP** for reading and replying to emails
- **GitLab API** for issue management
- **Hetzner VPS** (Ubuntu) with systemd

## Notes

- On Hetzner VPS port **465 is blocked** outbound — use SMTP port **587 (STARTTLS)**. Config branches automatically based on `SMTP_PORT`.
- Gemini conversation history is a rolling window of 40 messages (no clearing on actions).
- Email thread detection strips quoted parts by common markers (`>`, `On ... wrote:`, `Dňa ... napísal`, `-----Original Message-----`, Outlook `From:/Od:` blocks).
- Hardcoded reply signature is in `emails.py::send_reply` — edit there to change.
