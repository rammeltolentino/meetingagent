# Meeting Agent — Python / VS Code Setup

Same agent, same pipeline, rebuilt in plain Python so everything runs and is
demoable directly from VS Code — no Google Apps Script, no Google Cloud
project, no OAuth consent screens. Just a Gmail account and an App Password.

## Architecture

```
 Streamlit app (app.py)          run_agent_loop.py (separate terminal)
 ┌─────────────────────┐         ┌──────────────────────────────┐
 │ Submit Request       │        │ while True:                  │
 │ MeetingMaster Admin  │──────▶ │   process_requests()         │
 │ Agent Dashboard      │  data/ │   sleep(CHECK_INTERVAL)       │
 └─────────────────────┘  *.json└──────────────────────────────┘
                                            │
                                            ▼
                          agent.py: smtplib (send) / imaplib (read)
                                            │
                                            ▼
                                    Gmail (App Password auth)
```

`app.py` and `run_agent_loop.py` both import the same `agent.py` — there's
only one copy of the actual logic. The Streamlit app is for submitting
requests, managing MeetingMaster, and demoing; the loop script is what makes
it genuinely unattended once it's running.

## 1. Prerequisites

- Python 3.10+ (`python --version` to check)
- VS Code with the Python extension
- A Gmail account to run the trial from

## 2. Create a Gmail App Password

Plain `smtplib`/`imaplib` can't use your normal Gmail password — Google
requires an **App Password** instead:

1. On the Gmail account: **Google Account → Security → 2-Step Verification** — turn this on if it isn't already (App Passwords require it)
2. Still under Security: search for **App passwords**, create one, name it "Meeting Agent"
3. Copy the 16-character password shown — you won't see it again

## 3. Set up the project in VS Code

```bash
cd meeting-agent-python
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Gmail credentials aren't set in `.env` — you log into the app itself with
your Gmail address and the App Password from step 2 (see "Logging in"
below). `.env` only holds server-wide config; open it and leave
`OPENAI_API_KEY` blank unless you want AI-based date extraction — the
regex fallback works fine without it for straightforward replies.

### Logging in

`streamlit run app.py` now opens on a login screen instead of the tabs.
Enter your Gmail address and the App Password from step 2 — it's verified
against Gmail immediately (a real IMAP login attempt, so you find out right
away if something's wrong) and then kept only in that browser session,
never written to disk. `python run_agent_loop.py` asks for the same thing
on the command line (password input is hidden) since it has no browser
session to log into.

## 4. Add your real MeetingMaster data

Either edit `data/meetingmaster.json` directly, or launch the app (next
step) and use the **MeetingMaster Admin** tab — same effect, whichever is
easier to demo.

## 5. Run it

**Terminal 1 — the front-end:**
```bash
streamlit run app.py
```
Opens in your browser at `localhost:8501`.

**Terminal 2 — the unattended agent (optional but this is the whole point):**
```bash
python run_agent_loop.py
```
Leave this running. It checks for work every `CHECK_INTERVAL_SECONDS`
(default 30 min) and acts without anyone touching anything.

Both are visibly Python, run from VS Code's own terminal panes — nothing
about this setup uses a tool the course didn't cover.

## 6. Test safely before going live

`LIVE_MODE=false` in `.env` is the default — every "send" gets logged instead
of actually going out. Submit a test request, then either wait for
`run_agent_loop.py`'s next check or click **Run agent now** in the Agent
Dashboard tab, and read the log entries to confirm the content looks right.

## 7. Go live

Flip `LIVE_MODE=true` in `.env`, restart both terminals. From here:

1. Submitting a request emails the PA (subject tagged with the request ID
   so the reply can be matched back to it — ask the PA to just hit Reply)
2. Once they reply, the next check reads it, extracts the date, and moves
   the request to "Date Confirmed"
3. The next check after that emails all attendees the Notice of Meeting +
   Call for Agenda, with the previous MoM and a `.ics` calendar invite
   attached — recipients' mail clients handle "Add to Calendar" from the
   `.ics` automatically, no Google Calendar API involved

## Known limits worth mentioning in your write-up

- **Subject-line matching**: the agent finds the PA's reply by searching for
  the request ID still present in the subject line. Works as long as the
  reply keeps the original subject (true by default in Gmail/Outlook) —
  breaks if someone starts a fresh email instead of hitting Reply.
- **Date extraction accuracy**: handles clear single-date replies well
  ("14 August 2026" / "2026-08-14" / "14/8/2026"). Ambiguous replies
  ("either the 14th or 15th") may need a manual nudge — edit
  `confirmedDate` directly in `data/requests.json` if extraction fails.
  This is why every action gets logged: you can always see what happened.
- **Gmail sending limits**: personal accounts cap around 500 emails/day —
  not a concern at meeting-coordination volumes.
- **This is a personal/trial-Gmail build**, matching the constraint that the
  production Outlook-based version from the original proposal isn't
  available yet.

## For your assignment write-up

`git init && git add . && git commit -m "Initial meeting agent"` gives you a
real commit history. `PROJECT-BRIEF.md` (if present) has the fuller context
on how this evolved from the original proposal, in case you want to
reference the design decisions along the way.
