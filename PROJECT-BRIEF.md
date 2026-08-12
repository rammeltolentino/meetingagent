# Meeting Agent — Project Brief (Python version)

## Context
Originating proposal: "SCDF Email AI Assistant" — automating inter-departmental meeting
coordination. Trialed on personal Gmail due to government restrictions on the production
Outlook environment. Built for an assignment, where the coding needs to visibly happen in
VS Code using tools actually covered in the course (Python, VS Code, Google Colab).

## Why this version exists
Two earlier versions were built and superseded:
1. A single-file HTML/JS prototype using a Claude.ai artifact's browser storage — useful for
   demoing the concept interactively, no real persistence or real Gmail access of its own.
2. A Google Apps Script version (clasp-managed, Google Sheet + custom Web App front-end) —
   fully functional and genuinely unattended, but Apps Script wasn't a tool the course
   covered, which risked looking like an unexplained/unapproved dependency in review.

This version replaces (2) with **plain Python** — `smtplib`/`imaplib` against a Gmail App
Password instead of the Gmail API/OAuth, JSON files instead of Google Sheets, a `.ics` file
attachment instead of the Google Calendar API, and Streamlit instead of a Google Form or
custom web page. Same pipeline and behavior throughout — only the implementation changed.

## Architecture
`Streamlit app (submit + admin + dashboard) → JSON files → agent.py (smtplib/imaplib) → Gmail`
`run_agent_loop.py` runs `agent.py`'s `process_requests()` on a timer in a separate terminal
for real unattended operation — this is the direct equivalent of the Apps Script trigger.

## Files
| File | Purpose |
|---|---|
| `config.py` | Loads settings from `.env` (Gmail credentials, live/test mode, check interval) |
| `agent.py` | All core logic: send availability request, read PA reply, extract date, send Notice + `.ics` invite, `process_requests()` |
| `run_agent_loop.py` | Standalone script — run in its own terminal for unattended, timed operation |
| `app.py` | Streamlit front-end: Submit Request / MeetingMaster Admin / Agent Dashboard tabs |
| `data/meetingmaster.json` | The meeting types, Chairman/PA/attendee data — seeded with one "Test" entry |
| `data/requests.json` | Submitted requests and their pipeline status — starts empty |
| `.env.example` | Template for local secrets — copy to `.env`, never commit the real one |
| `README.md` | Full setup steps, including the Gmail App Password walkthrough |

## Current state
Code is written; not yet run end-to-end. `data/meetingmaster.json` has one seed entry
("Test" / PA `adlidafir27@gmail.com` / Chairman "Adli") carried over from earlier testing —
replace or add to this with real data before a real demo.

## What's left to do, in order
1. `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
2. Create a Gmail App Password (2-Step Verification must be on first) — steps in `README.md`
3. `cp .env.example .env`, fill in `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD`
4. Add/confirm real MeetingMaster data (edit the JSON directly, or via the Admin tab)
5. `streamlit run app.py` in one terminal — submit a test request
6. `python run_agent_loop.py` in a second terminal, or click "Run agent now" in the dashboard
7. With `LIVE_MODE=false` (default), confirm the log entries show the right content
8. Flip `LIVE_MODE=true` in `.env`, restart both, and re-test for real

## Known gotchas
- The PA's reply is matched by request ID still present in the subject line — ask PAs to
  hit Reply rather than starting a new email.
- Date extraction: AI-based via Anthropic API if `ANTHROPIC_API_KEY` is set, otherwise a
  regex fallback. Ambiguous phrasing may need `confirmedDate` set manually in the JSON.
- Gmail personal accounts cap around 500 sends/day — not a concern at this volume.

## What to do with this brief
Read it, then work through the checklist above. Ask before flipping `LIVE_MODE` to true or
sending any real email — everything up to that point is safe to run repeatedly.
