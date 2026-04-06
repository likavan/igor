# Igor

Personal AI assistant running on Hetzner VPS.

## Stack

- Python, python-telegram-bot, Anthropic SDK (Claude)
- APScheduler, SQLite
- Telegram bot interface

## Features

- Telegram bot with Claude-powered conversations
- Reminders stored in SQLite
- Daily summary at 8:00 (Europe/Bratislava)

## Setup

```bash
cd ~/assistant
source venv/bin/activate
python bot.py
```

Runs as systemd service: `systemctl restart assistant`
