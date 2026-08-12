"""
Central configuration — loaded from a local .env file (see .env.example).
Nothing here talks to Google Cloud, OAuth, or Apps Script — just plain
SMTP/IMAP settings for a Gmail account using an App Password.

Gmail credentials are NOT read from here — each user logs into the
Streamlit app with their own Gmail address + App Password, kept only in
that browser session (see app.py). Everything below is server-wide config
that isn't a per-user secret.
"""
import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # optional — regex fallback works without it

# Optional — gates the whole app behind one shared password, checked before
# the Gmail login screen even renders. Leave blank to leave the app open.
APP_ACCESS_PASSWORD = os.getenv("APP_ACCESS_PASSWORD")

# Optional — gates the MeetingMaster Admin tab behind a password. Leave blank
# to leave that tab open (matches the old, unprotected behavior).
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")

# Safety switch — mirrors the LIVE_MODE idea from the earlier Apps Script build.
# False: nothing actually sends, every "send" is just logged. True: real emails go out.
LIVE_MODE = os.getenv("LIVE_MODE", "false").lower() == "true"

# How often run_agent_loop.py checks for work, in seconds. 1800 = 30 minutes.
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "1800"))

DATA_DIR = "data"
UPLOADS_DIR = "uploads"
MEETINGMASTER_FILE = os.path.join(DATA_DIR, "meetingmaster.json")
REQUESTS_FILE = os.path.join(DATA_DIR, "requests.json")
