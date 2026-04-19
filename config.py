import os
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
YOUR_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Bratislava"))
IMAP_SERVER = os.getenv("IMAP_SERVER")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_EMAIL = os.getenv("IMAP_EMAIL")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD")
GITLAB_URL = os.getenv("GITLAB_URL", "").rstrip("/")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN")
GITLAB_USERNAME = os.getenv("GITLAB_USERNAME")
