# Igor

Personal AI assistant running as a Telegram bot, powered by Claude. Built to manage daily tasks, emails and work projects from a single chat interface.

## Features

### Claude-powered chat
Natural language conversations in Slovak. Ask anything or trigger actions by just describing what you need.

### Reminders
- Create reminders in natural language ("pripomen mi zajtra o 8:30 porada")
- Supports Slovak day names and relative dates (dnes, zajtra, pozajtra)
- Automatic check every 60 seconds for due reminders
- Daily morning summary at 8:00

### Email (IMAP)
- Check latest emails without marking them as read (IMAP PEEK)
- Unread emails highlighted with blue dot
- Read full email content via inline buttons
- Automatic hourly check for new emails (workdays 15:30-22:00)
- Tracks notified emails to avoid duplicate notifications

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
| `/h` | Help |

Or just write in natural language - Claude handles the rest.

## Project structure

```
bot.py          # Entry point, registers handlers and jobs
config.py       # Environment variables and constants
db.py           # SQLite operations (reminders, notified emails)
emails.py       # IMAP email fetching
gitlab.py       # GitLab API integration
handlers.py     # Telegram message and callback handlers
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
- **Claude** (claude-sonnet-4) via Anthropic SDK
- **SQLite** for reminders and email notification tracking
- **IMAP** for email access
- **GitLab API** for issue management
- **Hetzner VPS** (Ubuntu) with systemd
