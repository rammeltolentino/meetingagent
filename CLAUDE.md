# Meeting Agent — Instructions for Claude Code

Read `PROJECT-BRIEF.md` and `README.md` in this directory before making any changes — they
hold the full context: what this is, why it's built this way, and the setup checklist.

## Ground rules (do not deviate from these)

- **Course-approved tools only**: Python, VS Code, Google Colab. Do NOT introduce Google
  Apps Script, Google Cloud Console, OAuth flows, or any other tool outside that scope —
  that's specifically why this project exists in this form.
- Gmail access is plain `smtplib`/`imaplib` with an App Password (`.env`) — nothing more.
- `.env` holds real credentials — never print its contents back to me, never commit it,
  never put any part of it in a commit message, comment, or log statement.
- `LIVE_MODE` in `.env` defaults to `false` (test mode — nothing actually sends). Never
  change it to `true`, and never call a code path that sends real email, without asking me
  directly first, in plain language, and waiting for an explicit yes.
- Don't introduce new architecture or dependencies — extend what's here (`agent.py`,
  `app.py`, `run_agent_loop.py`) rather than replacing any of it, unless I explicitly ask
  for a redesign.

## Current state

Code is written but not yet run end-to-end. See "What's left to do, in order" in
`PROJECT-BRIEF.md` for the exact remaining checklist.
