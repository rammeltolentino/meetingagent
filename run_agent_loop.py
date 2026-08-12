"""
Run this in its own terminal to let the agent check and act on requests
automatically, on a timer — this is what makes it genuinely hands-off,
rather than something you have to trigger manually every time.

Usage:
    python run_agent_loop.py

Prompts once at startup for the Gmail address + App Password to run as
(kept only in memory for this process — never written to disk), same as
logging into the Streamlit app. Stop with Ctrl+C. Leave it running in a
VS Code terminal tab (or set it up as a scheduled task / cron job) for real
unattended operation.
"""
import getpass
import time
import config
from agent import process_requests, verify_gmail_login

if __name__ == "__main__":
    gmail_address = input("Gmail address: ").strip()
    gmail_app_password = getpass.getpass("Gmail App Password (16 characters, input hidden): ").strip()
    ok, error = verify_gmail_login(gmail_address, gmail_app_password)
    if not ok:
        print(f"Login failed: {error}")
        raise SystemExit(1)
    creds = (gmail_address, gmail_app_password)

    mode = "LIVE (real emails will send)" if config.LIVE_MODE else "TEST MODE (nothing actually sends)"
    print(f"\nMeeting Agent running as {gmail_address} — {mode}")
    print(f"Checking every {config.CHECK_INTERVAL_SECONDS} seconds. Ctrl+C to stop.\n")

    while True:
        try:
            process_requests(creds)
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Checked requests.")
        except Exception as e:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error during check: {e}")
        time.sleep(config.CHECK_INTERVAL_SECONDS)
