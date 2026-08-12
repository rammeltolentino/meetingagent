"""
Meeting Agent — core logic
============================
Plain Python: smtplib + imaplib against a Gmail account (App Password auth).
No Google Cloud project, no OAuth consent screen, no Apps Script.

Every function that touches Gmail takes `creds` as its first argument — a
(gmail_address, gmail_app_password) tuple for whichever user is logged in
via the Streamlit app's login form (see app.py). Nothing here reads a fixed
account from config/.env anymore; there's no server-side "default" account.

Pipeline (mirrors the original proposal):
  New                  -> send PA availability-request email
  Awaiting PA Response -> check inbox for PA's reply, extract date (AI + regex fallback)
  Date Confirmed       -> send Notice of Meeting + Call for Agenda (MoM attached + Google Calendar link)
  Collecting Agenda    -> waiting on attendee agenda-item replies until a manually-set deadline
  Agenda Compiled      -> done, compiled agenda draft created in Gmail Drafts
"""
import json
import os
import re
import smtplib
import imaplib
import email
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.utils import parseaddr
from html import escape as escape_html
from urllib.parse import urlencode

import config

STATUS_NEW = "New"
STATUS_AWAITING_PA = "Awaiting PA Response"
STATUS_DATE_CONFIRMED = "Date Confirmed"
STATUS_NOTICE_SENT = "Notice Sent"
STATUS_COLLECTING_AGENDA = "Collecting Agenda"
STATUS_AGENDA_COMPILED = "Agenda Compiled"

TEMPLATES = {
    "request_availability": """Good morning {pa}

May I kindly seek your inputs for some common dates for {chairman} for the next {meeting_type} please?

{quarter}/{year} {meeting_type}
Dates available:


Please reply directly to this email (keeping the subject line as-is) so it can be tracked.

Thank you for your assistance.

Best regards,
Meeting Coordination Agent""",

    "notice_agenda": """Dear all,

This is to inform you that the {meeting_type} meeting will be held on {confirmed_date}.

📅 Add to Google Calendar: {calendar_link}

Please submit your agenda items ahead of the meeting for consolidation into the agenda.

The minutes of the previous meeting are attached for reference.

Attendees: {attendees}

Best regards,
Meeting Coordination Agent"""
}


# ---------------------- storage ----------------------

def _load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_meetingmaster():
    return _load_json(config.MEETINGMASTER_FILE, [])


def save_meetingmaster(records):
    _save_json(config.MEETINGMASTER_FILE, records)


def load_requests():
    return _load_json(config.REQUESTS_FILE, [])


def save_requests(records):
    _save_json(config.REQUESTS_FILE, records)


def get_meetingmaster_by_type(meeting_type):
    for m in load_meetingmaster():
        if m["meetingType"] == meeting_type:
            return m
    return None


def append_log(req, note):
    req.setdefault("log", []).append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "note": note
    })


# ---------------------- login ----------------------

def verify_gmail_login(gmail_address, gmail_app_password):
    """Attempts a real IMAP login with the given credentials so the login
    form can give immediate feedback ("wrong password") instead of the user
    only finding out on the first send, deep in the pipeline. Doesn't send
    or read anything — just logs in and back out. Returns (True, None) or
    (False, error_message). Runs regardless of LIVE_MODE — logging in isn't
    a "send", so it's safe to always actually attempt."""
    try:
        imap = imaplib.IMAP4_SSL(config.IMAP_SERVER)
        try:
            imap.login(gmail_address, gmail_app_password)
        finally:
            imap.logout()
        return True, None
    except imaplib.IMAP4.error:
        return False, ("Login failed — check the Gmail address and App Password. "
                        "Make sure you're using a 16-character App Password, not your normal Gmail password "
                        "(needs 2-Step Verification enabled first).")
    except Exception as e:
        return False, f"Couldn't reach Gmail: {e}"


# ---------------------- sending mail ----------------------

def send_email(creds, to_addrs, subject, body, attachments=None, is_html=False):
    """creds: (gmail_address, gmail_app_password) for the logged-in user.
    attachments: list of (filename, bytes, mime_main, mime_sub).
    is_html: True when body is already-built HTML (e.g. a table) rather than
    plain text. Respects config.LIVE_MODE — when False, logs what WOULD be
    sent instead of actually sending, so you can test safely."""
    gmail_address, gmail_app_password = creds
    if isinstance(to_addrs, str):
        to_addrs = [to_addrs]

    if not config.LIVE_MODE:
        return f"[TEST MODE - not sent] To: {to_addrs} | Subject: {subject}"

    msg = MIMEMultipart()
    msg["From"] = gmail_address
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html" if is_html else "plain"))

    for filename, data, main, sub in (attachments or []):
        part = MIMEBase(main, sub)
        part.set_payload(data)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
        msg.attach(part)

    with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as server:
        server.starttls()
        server.login(gmail_address, gmail_app_password)
        server.sendmail(gmail_address, to_addrs, msg.as_string())
    return "sent"


def create_gmail_draft(creds, to_addrs, subject, body, is_html=False):
    """creds: (gmail_address, gmail_app_password) for the logged-in user.
    Saves a message into the Gmail Drafts folder via plain IMAP APPEND —
    no Google API/OAuth needed. The human opens it in Gmail, reviews/edits,
    and sends it themselves; the agent never sends it. Respects LIVE_MODE
    the same way send_email does (test mode just logs what would happen).
    Assumes the standard Gmail IMAP folder name "[Gmail]/Drafts" — if a
    Gmail account has a non-English locale or custom label setup this may
    need adjusting."""
    gmail_address, gmail_app_password = creds
    if isinstance(to_addrs, str):
        to_addrs = [to_addrs]

    if not config.LIVE_MODE:
        return f"[TEST MODE - no draft created] To: {to_addrs} | Subject: {subject}"

    msg = MIMEMultipart()
    msg["From"] = gmail_address
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html" if is_html else "plain"))

    imap = imaplib.IMAP4_SSL(config.IMAP_SERVER)
    try:
        imap.login(gmail_address, gmail_app_password)
        imap.append("[Gmail]/Drafts", "\\Draft", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    finally:
        imap.logout()
    return "draft created"


# ---------------------- reading the PA's reply ----------------------

def _extract_plain_text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True)
                return payload.decode(charset, errors="ignore") if payload else ""
        return ""
    charset = msg.get_content_charset() or "utf-8"
    payload = msg.get_payload(decode=True)
    return payload.decode(charset, errors="ignore") if payload else ""


_QUOTE_HEADER_RE = re.compile(r"^\s*On .{1,120}\bwrote:\s*$", re.IGNORECASE | re.MULTILINE)


def strip_quoted_reply(text):
    """Cuts the quoted original message that Gmail/Outlook/Apple Mail append
    below every reply (an 'On ... wrote:' header followed by '>'-prefixed
    lines), so date/time extraction only sees what the PA actually typed —
    otherwise it can match the quote header's own date/time instead of
    anything the PA proposed."""
    m = _QUOTE_HEADER_RE.search(text)
    if m:
        text = text[:m.start()]
    lines = [line for line in text.splitlines() if not line.strip().startswith(">")]
    return "\n".join(lines).strip()


def find_pa_reply(creds, pa_email, request_id):
    """creds: (gmail_address, gmail_app_password) for the logged-in user.
    Searches INBOX for a message from the PA whose subject still carries
    the request ID tag (Gmail/Outlook preserve the subject on Reply by default)."""
    gmail_address, gmail_app_password = creds
    imap = imaplib.IMAP4_SSL(config.IMAP_SERVER)
    try:
        imap.login(gmail_address, gmail_app_password)
        imap.select("INBOX")
        typ, data = imap.search(None, f'(FROM "{pa_email}" SUBJECT "{request_id}")')
        ids = data[0].split()
        if not ids:
            return None
        typ, msg_data = imap.fetch(ids[-1], "(RFC822)")
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        return _extract_plain_text(msg)
    finally:
        imap.logout()


# ---------------------- date extraction ----------------------

def extract_datetime_with_ai(email_text):
    """Uses the OpenAI API if OPENAI_API_KEY is set; returns (date, start_time,
    end_time) as ('YYYY-MM-DD', 'HH:MM', 'HH:MM' 24-hour), or None (falls
    through to regex) if the key is missing, no date is found, or the call
    fails for any reason."""
    if not config.OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=20,
            messages=[{
                "role": "user",
                "content": (
                    f"Today's date is {datetime.now().strftime('%Y-%m-%d')} (a "
                    f"{datetime.now().strftime('%A')}) — use this to resolve any "
                    "relative date the email gives, e.g. \"tomorrow\", \"next Tuesday\", "
                    "\"in two weeks\". "
                    "Extract the proposed meeting date and time slot from this email — "
                    "the PA is replying with the chairman's availability. "
                    "Times may be written as 24-hour military time without a colon, "
                    "e.g. \"0900 to 1030 hrs\" means start time 09:00 and end time 10:30. "
                    "Reply with ONLY 'YYYY-MM-DD HH:MM HH:MM' (date, start time, end "
                    "time) using 24-hour time, nothing else. If no start time is "
                    "mentioned, use 09:00. Only if the email gives a single time with "
                    "no end time or range at all, output an end time exactly 60 minutes "
                    "after the start. If no date is proposed, reply with NONE.\n\nEmail:\n" + email_text
                )
            }]
        )
        text = resp.choices[0].message.content.strip()
        m = re.match(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) (\d{2}:\d{2})$", text)
        return (m.group(1), m.group(2), m.group(3)) if m else None
    except Exception:
        return None


def extract_agenda_item_with_ai(email_text):
    """Uses the OpenAI API if OPENAI_API_KEY is set to pull a structured
    agenda-item submission out of an attendee's free-text reply — no fixed
    table format required, unlike a regex/table parser. Returns a dict with
    title/presenter/department/purpose/duration/synopsis (empty string for
    any field not mentioned), or None if the key is missing, the reply
    doesn't contain an agenda item, or the call fails for any reason."""
    if not config.OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": (
                    "This is an attendee's email reply submitting an agenda item for a "
                    "meeting. Extract it as a JSON object with exactly these keys: "
                    "title, presenter, department, purpose, duration, synopsis. "
                    "Use an empty string for any field not mentioned. 'duration' should "
                    "look like '15 mins'. If this reply does not actually submit an "
                    "agenda item, reply with exactly NONE. Reply with ONLY the JSON "
                    "object (or NONE), nothing else.\n\nEmail:\n" + email_text
                )
            }]
        )
        text = resp.choices[0].message.content.strip()
        if text == "NONE":
            return None
        data = json.loads(text)
        return {
            "title": data.get("title", ""),
            "presenter": data.get("presenter", ""),
            "department": data.get("department", ""),
            "purpose": data.get("purpose", ""),
            "duration": data.get("duration", ""),
            "synopsis": data.get("synopsis", ""),
        }
    except Exception:
        return None


_MONTH_ABBR_FIXUPS = {"sept": "sep"}  # non-standard abbreviations Python's %b doesn't recognize


def extract_date_fallback(text):
    # Normalize ordinal day suffixes ("24th" -> "24") so the patterns below,
    # none of which understand "st"/"nd"/"rd"/"th", can still match.
    text = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)
    # Normalize non-standard month abbreviations (e.g. "Sept" -> "Sep") so
    # %b can parse them; strptime itself is already case-insensitive.
    for wrong, right in _MONTH_ABBR_FIXUPS.items():
        text = re.sub(rf"\b{wrong}\b", right, text, flags=re.IGNORECASE)

    patterns_and_formats = [
        (r"\b(\d{4}-\d{1,2}-\d{1,2})\b", ["%Y-%m-%d"]),
        (r"\b(\d{4}/\d{1,2}/\d{1,2})\b", ["%Y/%m/%d"]),
        (r"\b(\d{1,2}\s+[A-Za-z]+\s+\d{4})\b", ["%d %B %Y", "%d %b %Y"]),
        (r"\b([A-Za-z]+\s+\d{1,2},?\s+\d{4})\b", ["%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"]),
        (r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{4})\b", ["%d/%m/%Y", "%d-%m-%Y"]),
        (r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2})\b", ["%d/%m/%y", "%d-%m-%y"]),
    ]
    for pattern, fmts in patterns_and_formats:
        m = re.search(pattern, text)
        if m:
            for fmt in fmts:
                try:
                    return datetime.strptime(m.group(1), fmt).strftime("%Y-%m-%d")
                except ValueError:
                    continue
    return None


_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6}


def extract_relative_date_fallback(text, today=None):
    """Resolves simple relative-date phrases ('tomorrow', 'next Tuesday',
    'this Friday', a bare weekday name) against the real calendar in Python
    — deliberately NOT left to the AI, since day-of-week arithmetic is a
    known weak spot for small LLMs (verified: gpt-4o-mini gets it wrong).
    Returns 'YYYY-MM-DD' or None."""
    today = today or datetime.now()
    t = text.lower()

    if re.search(r"\btoday\b", t):
        return today.strftime("%Y-%m-%d")
    if re.search(r"\btomorrow\b", t):
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    m = re.search(
        r"\b(?:next|this|coming)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        t
    )
    if not m:
        m = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t)
    if m:
        target = _WEEKDAYS[m.group(1)]
        days_ahead = (target - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7  # same weekday as today -> the next occurrence, not today
        return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    return None


_RANGE_SEP = r"(?:-|–|—|to|until|till|~)"


def _to_24h(hour, minute, ampm):
    hour = int(hour)
    if ampm:
        ampm = ampm.lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
    return f"{hour:02d}:{minute or '00'}"


# Matches one clock time written any of: "9", "9:30", "9.30", or bare digits
# glued together with no separator at all — "930" or "1030" (read as HH+MM
# from the right, same convention as saying it out loud without a colon).
_TIME_TOKEN = r"(\d{1,2}(?:[:.]\d{2})?|\d{3,4})"


def _parse_hm_token(token):
    """Splits a _TIME_TOKEN match into (hour_str, minute_str). Bare 3-4
    digit tokens split from the right ('930' -> 9, 30; '1030' -> 10, 30).
    Returns None if the minute half isn't a valid 00-59 (so the caller can
    treat it as no match rather than silently accepting nonsense)."""
    if ":" in token or "." in token:
        hour, minute = re.split(r"[:.]", token)
    elif len(token) <= 2:
        hour, minute = token, "00"
    else:
        hour, minute = token[:-2], token[-2:]
    return (hour, minute) if int(minute) <= 59 else None


def extract_time_fallback(text):
    """Returns 'HH:MM' in 24-hour time, or None if no time-of-day found."""
    m = re.search(rf"(?<!\d){_TIME_TOKEN}\s*([APap][Mm])\b", text, re.IGNORECASE)
    if m:
        parsed = _parse_hm_token(m.group(1))
        if parsed:
            return _to_24h(parsed[0], parsed[1], m.group(2))

    # Bare military time, e.g. "0900hrs" / "0900 hrs" — the "hrs" anchor
    # keeps this from false-positive matching an unrelated 4-digit number
    # like a year.
    m = re.search(r"(?<!\d)([01]\d|2[0-3])([0-5]\d)\s*hrs?\b", text, re.IGNORECASE)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"

    m = re.search(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)", text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"

    return None


def extract_time_range_fallback(text):
    """Returns (start 'HH:MM', end 'HH:MM' or None) in 24-hour time. Looks
    for an explicit time range first — with AM/PM attached per side, each
    side written as 'H', 'H:MM', 'H.MM', or bare glued digits like '930'/
    '1030' with no separator at all (e.g. '9am - 11am', '9am to 1030am',
    '2 - 4pm') — or as bare digits with colon/dot optional and an optional
    single trailing AM/PM and/or 'hrs' marker covering both sides (e.g.
    '1345 to 1500hrs', '130 to 250 pm', '0900-1700'). Falls back to a single
    start time via extract_time_fallback with end left as None, so the
    caller can decide a default duration."""
    m = re.search(
        rf"(?<!\d){_TIME_TOKEN}\s*([APap][Mm])?\s*{_RANGE_SEP}\s*{_TIME_TOKEN}\s*([APap][Mm])\b",
        text, re.IGNORECASE
    )
    if m:
        tok1, ap1, tok2, ap2 = m.groups()
        parsed1, parsed2 = _parse_hm_token(tok1), _parse_hm_token(tok2)
        if parsed1 and parsed2:
            ap1 = ap1 or ap2  # inherit AM/PM if only stated on the end, e.g. "9 - 11am"
            return _to_24h(*parsed1, ap1), _to_24h(*parsed2, ap2)

    m = re.search(
        rf"(?<!\d)([01]?\d|2[0-3])[:.]?([0-5]\d)\s*{_RANGE_SEP}\s*"
        r"([01]?\d|2[0-3])[:.]?([0-5]\d)\s*([APap][Mm])?(?:\s*hrs?\.?)?(?!\d)",
        text, re.IGNORECASE
    )
    if m:
        h1, m1, h2, m2, ap = m.groups()
        if ap:
            # A single trailing AM/PM applies to both ends, e.g. "130 to 250 pm"
            # means 1:30 PM to 2:50 PM — treat as 12-hour, not raw 24-hour digits.
            return _to_24h(h1, m1, ap), _to_24h(h2, m2, ap)
        return f"{int(h1):02d}:{m1}", f"{int(h2):02d}:{m2}"

    return extract_time_fallback(text), None


# ---------------------- Google Calendar link (no Calendar API/OAuth needed) ----------------------

def build_google_calendar_link(summary, start_date, start_time, duration_minutes=60, details=""):
    """Builds a Google Calendar 'quick add' link — clicking it opens Google
    Calendar with the event pre-filled for the recipient to save themselves.
    Just a URL, no Google Calendar API/OAuth needed. Replaces the earlier
    .ics attachment approach, which rendered inconsistently between Gmail
    and Outlook."""
    start_dt = datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    fmt = lambda d: d.strftime("%Y%m%dT%H%M%S")
    params = {
        "action": "TEMPLATE",
        "text": summary,
        "dates": f"{fmt(start_dt)}/{fmt(end_dt)}",
    }
    if details:
        params["details"] = details
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


# ---------------------- pipeline stages ----------------------

def render_default_availability_email(mt, quarter, year):
    """Subject (without the [REQ-xxxx] tracking tag — that's appended at send
    time) and body for the availability-request email, as they'd look with no
    customization. Used both to actually send and to show a live preview in
    the Streamlit 'Advanced' editor before a request is submitted."""
    subject = f"[For {mt['chairmanName']}'s Availability] {quarter}/{year} {mt['meetingType']}"
    body = TEMPLATES["request_availability"].format(
        pa=mt["paName"], chairman=mt["chairmanName"],
        meeting_type=mt["meetingType"], quarter=quarter, year=year
    )
    return subject, body


def send_availability_request(creds, req, mt):
    default_subject, default_body = render_default_availability_email(mt, req["quarter"], req["year"])
    subject_base = req.get("customSubject") or default_subject
    body = req.get("customBody") or default_body
    subject = f"{subject_base} [{req['id']}]"

    result = send_email(creds, mt["paEmail"], subject, body)
    req["status"] = STATUS_AWAITING_PA
    req["paContactedAt"] = datetime.now().isoformat(timespec="seconds")
    # Consumed for this stage — clear so it doesn't leak into the next send stage's default.
    req["customSubject"] = None
    req["customBody"] = None
    append_log(req, f"Availability request to {mt['paEmail']}: {result}")


def check_for_pa_reply(creds, req, mt):
    reply_text = find_pa_reply(creds, mt["paEmail"], req["id"])
    if not reply_text:
        return  # nothing yet — checked again on the next run

    reply_text = strip_quoted_reply(reply_text)

    # Deterministic parsing first, AI as last resort: for explicit numeric
    # dates/times (nearly always what a PA sends) and common relative
    # phrases ("tomorrow", "next Tuesday"), Python's own date math and the
    # regex parser are actually MORE reliable than a small LLM — e.g.
    # gpt-4o-mini both misreads colon-less ranges like "130 to 250 pm" as
    # rounding down to whole hours, AND gets weekday arithmetic wrong (it
    # once computed "next Tuesday" as a Sunday). AI only handles phrasing
    # neither of those can parse at all (e.g. "the day after Diwali").
    date_str = extract_date_fallback(reply_text) or extract_relative_date_fallback(reply_text)
    if date_str:
        start_time, end_time = extract_time_range_fallback(reply_text)
        start_time = start_time or "09:00"
        if not end_time:
            end_dt = datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M") + timedelta(hours=1)
            end_time = end_dt.strftime("%H:%M")
    else:
        ai_result = extract_datetime_with_ai(reply_text)
        if ai_result:
            date_str, start_time, end_time = ai_result
        else:
            append_log(req, "PA replied, but no date could be extracted automatically. Check the reply and set confirmedDate manually.")
            return

    req["status"] = STATUS_DATE_CONFIRMED
    req["confirmedDate"] = date_str
    req["confirmedTime"] = start_time
    req["confirmedEndTime"] = end_time
    append_log(req, f"Date/time extracted from PA reply: {date_str} {start_time}-{end_time}")


def render_default_notice_email(req, mt):
    """Subject and body for the Notice of Meeting + Call for Agenda email, as
    they'd look with no customization. Only meaningful once confirmedDate is
    set. Used both to actually send and to show a live preview for review."""
    confirmed_time = req.get("confirmedTime", "09:00")
    start_dt = datetime.strptime(f"{req['confirmedDate']} {confirmed_time}", "%Y-%m-%d %H:%M")
    confirmed_end_time = req.get("confirmedEndTime")
    end_dt = (datetime.strptime(f"{req['confirmedDate']} {confirmed_end_time}", "%Y-%m-%d %H:%M")
              if confirmed_end_time else start_dt + timedelta(hours=1))
    date_display = f"{start_dt.strftime('%d %b %Y, %I:%M %p')} - {end_dt.strftime('%I:%M %p')}"
    subject = (f"[Notice of Meeting and Call for Agenda] {req['quarter']}/{req['year']} {mt['meetingType']} "
               f"on {start_dt.strftime('%d %b %Y')}, {start_dt.strftime('%I:%M %p')}")
    duration_minutes = int((end_dt - start_dt).total_seconds() // 60)
    calendar_link = build_google_calendar_link(
        f"{mt['meetingType']} Meeting", req["confirmedDate"], confirmed_time,
        duration_minutes=duration_minutes
    )
    body = TEMPLATES["notice_agenda"].format(
        meeting_type=mt["meetingType"], confirmed_date=date_display,
        attendees=", ".join(a["name"] for a in mt["attendees"]),
        calendar_link=calendar_link
    )
    return subject, body


def send_notice_and_agenda(creds, req, mt):
    attendee_emails = [a["email"] for a in mt["attendees"] if a.get("email")]
    if not attendee_emails:
        append_log(req, "No attendees with an email configured for this meeting type — add at least one in MeetingMaster Admin, then run the agent again.")
        return

    default_subject, default_body = render_default_notice_email(req, mt)
    subject_base = req.get("customSubject") or default_subject
    body = req.get("customBody") or default_body
    # Tagged with the tracking ID (same pattern as the availability-request
    # email) so agenda-item replies to this email can be matched back later.
    subject = f"{subject_base} [{req['id']}]"

    attachments = []
    mom_path = req.get("previousMoMPath")
    if mom_path and os.path.exists(mom_path):
        with open(mom_path, "rb") as f:
            attachments.append((os.path.basename(mom_path), f.read(), "application", "octet-stream"))

    result = send_email(creds, attendee_emails, subject, body, attachments)

    req["status"] = STATUS_COLLECTING_AGENDA
    req["noticeSentAt"] = datetime.now().isoformat(timespec="seconds")
    # Consumed for this stage — clear so it doesn't leak into a later send stage's default.
    req["customSubject"] = None
    req["customBody"] = None
    append_log(req, f"Notice + Google Calendar link to {len(attendee_emails)} attendees: {result}")


# ---------------------- agenda-item collection & compilation ----------------------

def parse_agenda_reply(body_text):
    """Extracts one agenda item (title, presenter, department, purpose,
    duration, synopsis) from a single attendee's reply body, via AI — no
    fixed table format required. Quote-strips the reply first so only what
    the attendee actually typed gets read. Returns None if OPENAI_API_KEY
    isn't set, the reply doesn't contain an agenda item, or extraction fails
    (in which case the reply is still found by collect_agenda_replies but
    not recorded as a submission)."""
    return extract_agenda_item_with_ai(strip_quoted_reply(body_text))


def collect_agenda_replies(creds, req, mt):
    """creds: (gmail_address, gmail_app_password) for the logged-in user.
    Finds attendee replies to the Notice/Call-for-Agenda email — matched
    by the same [REQ-xxxx] tracking tag used everywhere else — and records
    any that parse_agenda_reply can make sense of. Just reads the inbox, no
    send involved, so this runs automatically regardless of auto/manual mode
    (same as check_for_pa_reply). Returns True if anything new was recorded."""
    gmail_address, gmail_app_password = creds
    seen_uids = {s.get("messageUid") for s in req.get("agendaSubmissions", [])}
    imap = imaplib.IMAP4_SSL(config.IMAP_SERVER)
    changed = False
    try:
        imap.login(gmail_address, gmail_app_password)
        imap.select("INBOX")
        typ, data = imap.search(None, f'(SUBJECT "{req["id"]}")')
        ids = data[0].split()
        for msg_id in ids:
            uid = msg_id.decode()
            if uid in seen_uids:
                continue
            typ, msg_data = imap.fetch(msg_id, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            name, addr = parseaddr(msg.get("From", ""))
            body_text = _extract_plain_text(msg)

            parsed = parse_agenda_reply(body_text)
            if parsed:
                req.setdefault("agendaSubmissions", []).append({
                    "messageUid": uid,
                    "senderName": name or addr,
                    "senderEmail": addr,
                    "receivedAt": datetime.now().isoformat(timespec="seconds"),
                    **parsed
                })
                append_log(req, f"{name or addr} submitted an agenda item titled '{parsed.get('title', '?')}' — check your email.")
                changed = True
    finally:
        imap.logout()
    return changed


def _parse_duration_minutes(duration_str):
    m = re.search(r"(\d+)", duration_str or "")
    return int(m.group(1)) if m else 0


def build_agenda_json(req, mt):
    """Normalizes collected agenda submissions into the JSON structure the
    user specified — used both for record-keeping (stored on the request)
    and as the input to render_compiled_agenda_email."""
    return {
        "meeting_number": f"{req['quarter']}/{req['year']}",
        "forum": mt["meetingType"],
        "date": req["confirmedDate"],
        "time": req.get("confirmedTime", "09:00"),
        "venue": mt.get("venue") or "TBC",
        "agenda_items": [
            {
                "title": s.get("title", ""),
                "presenter": s.get("presenter", ""),
                "department": s.get("department", ""),
                "purpose": s.get("purpose", ""),
                "duration": s.get("duration", ""),
                "synopsis": s.get("synopsis", ""),
            }
            for s in req.get("agendaSubmissions", [])
        ],
    }


def render_compiled_agenda_email(req, mt, agenda_json):
    """Renders the final compiled Call-for-Agenda email in the exact format
    supplied by the user: chairman salutation, meeting header line, a
    real HTML <table> of agenda items (fixed 'Confirmation of Minutes' first
    row + Matters Arising note, then one row per submitted item), and a
    total-duration row — sent as HTML (not plain text) so the table actually
    renders as a table in Gmail/Outlook instead of showing literal '|' characters.
    Returns (subject, html_body); pass is_html=True wherever this body is sent."""
    meeting_dt = datetime.strptime(f"{agenda_json['date']} {agenda_json['time']}", "%Y-%m-%d %H:%M")
    date_display = meeting_dt.strftime("%d %B %Y")
    time_display = meeting_dt.strftime("%H%M")
    chairman = mt["chairmanName"]

    matters_arising_note = req.get("mattersArisingNote") or "There are no Matters Arising."

    def esc(v):
        return escape_html(str(v))

    row_style = 'style="border:1px solid #999; padding:6px; text-align:left; vertical-align:top;"'
    header_style = 'style="border:1px solid #999; padding:6px; text-align:left; background:#f0f0f0;"'

    header_row = "<tr>" + "".join(f"<th {header_style}>{esc(h)}</th>" for h in
                                   ["S/N", "Agenda Item", "Presenter", "Purpose/Synopsis", "Duration"]) + "</tr>"

    first_row = (
        "<tr>"
        f"<td {row_style}>1</td>"
        f"<td {row_style}>Confirmation of Minutes from Previous Meeting</td>"
        f"<td {row_style}>Secretariat</td>"
        f"<td {row_style}>For Confirmation. {esc(matters_arising_note)}</td>"
        f"<td {row_style}>5 mins</td>"
        "</tr>"
    )

    item_rows = []
    total_minutes = 5
    for i, item in enumerate(agenda_json["agenda_items"], start=2):
        duration_str = item.get("duration", "")
        total_minutes += _parse_duration_minutes(duration_str)
        purpose_synopsis = item.get("purpose", "")
        if item.get("synopsis"):
            purpose_synopsis += f" Synopsis: {item['synopsis']}"
        item_rows.append(
            "<tr>"
            f"<td {row_style}>{i}</td>"
            f"<td {row_style}>{esc(item.get('title', ''))}</td>"
            f"<td {row_style}>{esc(item.get('presenter', ''))}</td>"
            f"<td {row_style}>{esc(purpose_synopsis)}</td>"
            f"<td {row_style}>{esc(duration_str)}</td>"
            "</tr>"
        )

    total_label_style = 'style="border:1px solid #999; padding:6px; text-align:right; font-weight:bold;"'
    total_row = (
        "<tr>"
        f'<td colspan="4" {total_label_style}>Total Duration</td>'
        f"<td {row_style}><b>{total_minutes} mins</b></td>"
        "</tr>"
    )

    table = (
        '<table style="border-collapse: collapse; width:100%; font-family: Arial, sans-serif; font-size: 14px;">'
        + header_row + first_row + "".join(item_rows) + total_row +
        "</table>"
    )

    meeting_label = f"{req['quarter']} {mt['meetingType']}"
    subject = f"[For {chairman}'s Approval] Agenda for {meeting_label}"
    body = f"""<p>{esc(chairman)} Sir,</p>
<p><b>[FOR {esc(chairman.upper())}'S APPROVAL] AGENDA FOR {esc(meeting_label.upper())}</b></p>
<p>The {esc(meeting_label)} is scheduled on {esc(date_display)}, {esc(time_display)} hrs at {esc(agenda_json['venue'])}.</p>
<p>Please find the proposed agenda appended in the table below.</p>
{table}
<p>The secretariat seeks {esc(chairman)}'s approval for the agenda appended in the table above please.</p>
<p>Thank you, {esc(chairman)} Sir.</p>
<p>Thanks and Regards</p>"""

    return subject, body


def compile_agenda_draft(creds, req, mt):
    """creds: (gmail_address, gmail_app_password) for the logged-in user.
    Compiles every agenda item collected so far into one email and saves
    it as a Gmail draft (never auto-sent) for the user to review/edit before
    seeking the chairman's approval. Triggered manually from the dashboard —
    not gated on the agendaDeadline having passed, since that date is only
    a target for the user to decide 'now' by, not an automatic trigger."""
    agenda_json = build_agenda_json(req, mt)
    req["agendaCompiledJson"] = agenda_json
    subject, body = render_compiled_agenda_email(req, mt, agenda_json)

    chairman_email = mt.get("chairmanEmail") or creds[0]
    result = create_gmail_draft(creds, chairman_email, subject, body, is_html=True)

    req["status"] = STATUS_AGENDA_COMPILED
    req["agendaCompiledAt"] = datetime.now().isoformat(timespec="seconds")
    append_log(req, f"Compiled agenda ({len(agenda_json['agenda_items'])} item(s)) — {result}")


# ---------------------- main loop ----------------------

def advance_request(creds, req, mt, force_send=False):
    """Advances a single request by one stage, if it's ready. A request in
    'manual' mode (req['mode'] == 'manual') pauses right before either send
    stage instead of sending automatically — the Streamlit dashboard's
    per-request Review & Send flow is what sets force_send=True once a human
    has confirmed (and possibly edited) the email content. Returns True if
    anything actually changed."""
    is_manual = req.get("mode", "auto") == "manual"

    if req["status"] == STATUS_NEW:
        if is_manual and not force_send:
            return False
        send_availability_request(creds, req, mt)
        return True
    elif req["status"] == STATUS_AWAITING_PA:
        before = req["status"]
        check_for_pa_reply(creds, req, mt)  # just reads the inbox — no send, no review needed
        return req["status"] != before
    elif req["status"] == STATUS_DATE_CONFIRMED:
        if is_manual and not force_send:
            return False
        send_notice_and_agenda(creds, req, mt)
        return True
    elif req["status"] == STATUS_COLLECTING_AGENDA:
        return collect_agenda_replies(creds, req, mt)  # just reads the inbox — no send, no review needed
    return False


_TERMINAL_STATUSES = (STATUS_NOTICE_SENT, STATUS_AGENDA_COMPILED)


def process_requests(creds):
    """creds: (gmail_address, gmail_app_password) for the logged-in user.
    One pass over every request, advancing each one through as many stages
    as are immediately ready (e.g. a reply that's already in the inbox, or
    attendees that were just added) — so a single call drives a request as
    far as it can go without waiting on a future external event (a PA reply
    that hasn't arrived yet) or, for 'manual' mode requests, a human's
    explicit send confirmation from the dashboard. Call this repeatedly (see
    run_agent_loop.py) for unattended operation."""
    requests_data = load_requests()
    changed = False

    for req in requests_data:
        mt = get_meetingmaster_by_type(req["meetingType"])
        if not mt:
            append_log(req, f"No MeetingMaster entry found for '{req['meetingType']}'.")
            changed = True
            continue

        last_status = None
        while req["status"] != last_status and req["status"] not in _TERMINAL_STATUSES:
            last_status = req["status"]
            try:
                if advance_request(creds, req, mt):
                    changed = True
                else:
                    break
            except Exception as e:
                append_log(req, f"ERROR: {e}")
                changed = True
                break

    if changed:
        save_requests(requests_data)

    return requests_data


def process_single_request(creds, req_id, force_send=False, override_subject=None, override_body=None):
    """creds: (gmail_address, gmail_app_password) for the logged-in user.
    Advances exactly one request — used by the dashboard's per-request Run
    button. override_subject/override_body (from the Review & Send editor)
    are stored onto the request before sending, so send_availability_request/
    send_notice_and_agenda pick them up the same way a Submit-time custom
    email would."""
    requests_data = load_requests()
    req = next((r for r in requests_data if r["id"] == req_id), None)
    if req is None:
        return None

    if override_subject is not None:
        req["customSubject"] = override_subject
    if override_body is not None:
        req["customBody"] = override_body

    mt = get_meetingmaster_by_type(req["meetingType"])
    if not mt:
        append_log(req, f"No MeetingMaster entry found for '{req['meetingType']}'.")
        save_requests(requests_data)
        return req

    last_status = None
    while req["status"] != last_status and req["status"] not in _TERMINAL_STATUSES:
        last_status = req["status"]
        try:
            if not advance_request(creds, req, mt, force_send=force_send):
                break
        except Exception as e:
            append_log(req, f"ERROR: {e}")
            break

    save_requests(requests_data)
    return req


def trigger_agenda_compile(creds, req_id):
    """creds: (gmail_address, gmail_app_password) for the logged-in user.
    Dashboard entry point for the 'Compile & Create Draft' button — a
    deliberate, separate action from the auto-pipeline sweep above (never
    triggered by process_requests/run_agent_loop.py on its own), since
    creating the chairman-approval draft should always be a human's call."""
    requests_data = load_requests()
    req = next((r for r in requests_data if r["id"] == req_id), None)
    if req is None:
        return None

    mt = get_meetingmaster_by_type(req["meetingType"])
    if not mt:
        append_log(req, f"No MeetingMaster entry found for '{req['meetingType']}'.")
    elif req["status"] == STATUS_COLLECTING_AGENDA:
        try:
            compile_agenda_draft(creds, req, mt)
        except Exception as e:
            append_log(req, f"ERROR: {e}")

    save_requests(requests_data)
    return req
