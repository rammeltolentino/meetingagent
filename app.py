"""
Meeting Agent — Streamlit front-end
=====================================
Run with:  streamlit run app.py

Requires logging in with a Gmail address + App Password first (kept only in
that browser session, never written to disk) — see the login gate below.
Then: three tabs — submit a request, manage the MeetingMaster list, and a
dashboard where each request has its own Run/Review button, processed
independently and in any order. For real unattended operation, run
run_agent_loop.py in a separate terminal alongside this (it prompts for its
own login on the command line).
"""
import os
import re
import uuid
from datetime import datetime

import streamlit as st

import config
from utility import check_password
from agent import (
    load_meetingmaster, save_meetingmaster, load_requests, save_requests,
    get_meetingmaster_by_type, process_single_request, trigger_agenda_compile,
    verify_gmail_login,
    STATUS_NEW, STATUS_AWAITING_PA, STATUS_DATE_CONFIRMED, STATUS_NOTICE_SENT,
    STATUS_COLLECTING_AGENDA, STATUS_AGENDA_COMPILED,
    render_default_availability_email, render_default_notice_email
)


def parse_attendees_text(text):
    """Splits on comma or semicolon; a line only counts as an attendee if the
    second part looks like an email (has an '@'). Returns (attendees, skipped_lines)
    so the caller can show the user exactly what wasn't recognized."""
    attendees, skipped = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"[,;]", line, maxsplit=1)
        if len(parts) == 2 and "@" in parts[1]:
            attendees.append({"name": parts[0].strip(), "email": parts[1].strip()})
        else:
            skipped.append(line)
    return attendees, skipped

st.set_page_config(page_title="Meeting Agent Console", page_icon="📋", layout="centered")

if not check_password():
    st.stop()

st.markdown("""
<style>
html { font-size: 118%; }
button, .stButton button { font-size: 1.05rem !important; }
</style>
""", unsafe_allow_html=True)

st.title("📋 Meeting Agent Console")
st.caption("Inter-department meeting coordination — Gmail trial build")

# ==================== Login ====================
if not st.session_state.get("logged_in"):
    st.subheader("🔐 Log in with your Gmail")
    st.caption("Uses a Gmail App Password (plain SMTP/IMAP, no OAuth) — not your normal Gmail password. "
               "Kept only for this browser session; never written to disk.")
    login_email = st.text_input("Gmail address", key="login_email_input")
    login_app_pw = st.text_input("Gmail App Password (16 characters)", type="password", key="login_pw_input")
    if st.button("Log in", type="primary"):
        if not login_email or not login_app_pw:
            st.error("Enter both your Gmail address and App Password.")
        else:
            with st.spinner("Checking credentials..."):
                ok, error = verify_gmail_login(login_email, login_app_pw)
            if ok:
                st.session_state["gmail_address"] = login_email
                st.session_state["gmail_app_password"] = login_app_pw
                st.session_state["logged_in"] = True
                st.session_state.pop("login_pw_input", None)
                st.toast(f"Logged in as {login_email}", icon="✅")
                st.rerun()
            else:
                st.error(error)
    st.stop()

creds = (st.session_state["gmail_address"], st.session_state["gmail_app_password"])

login_col, logout_col = st.columns([5, 1])
login_col.success(f"✅ You are logged in as **{st.session_state['gmail_address']}**")
if logout_col.button("Log out"):
    for key in ("logged_in", "gmail_address", "gmail_app_password"):
        st.session_state.pop(key, None)
    st.rerun()

tab_submit, tab_admin, tab_dashboard = st.tabs(["Submit Request", "MeetingMaster Admin", "Agent Dashboard"])

# ==================== Submit Request ====================
with tab_submit:
    st.subheader("New meeting request")

    if st.session_state.get("last_submitted_id"):
        st.success(f"✅ Request submitted — ID `{st.session_state['last_submitted_id']}`. "
                   "The agent will pick it up on its next run.")
        del st.session_state["last_submitted_id"]

    meetingmaster = load_meetingmaster()

    if not meetingmaster:
        st.warning("No meeting types set up yet — add one in the MeetingMaster Admin tab first.")
    else:
        names = [m["meetingType"] for m in meetingmaster]
        meeting_type = st.selectbox("Meeting type", names, key="submit_meeting_type")
        col1, col2 = st.columns(2)
        with col1:
            quarter = st.selectbox("Quarter", ["Q1", "Q2", "Q3", "Q4"],
                                    index=(datetime.now().month - 1) // 3, key="submit_quarter")
        with col2:
            year = int(st.number_input("Year", value=datetime.now().year, step=1, key="submit_year"))
        mom_file = st.file_uploader("Previous minutes of meeting", type=["txt", "pdf", "docx"], key="submit_mom")

        mode_choice = st.radio(
            "Sending mode",
            ["Fully automatic", "Manual (review before sending)"],
            key="submit_mode", horizontal=True
        )
        st.caption("Manual mode pauses before **both** the availability-request and the final Notice email — "
                   "you'll need to click Review on the Agent Dashboard and confirm (or edit) each one before it sends.")

        if st.button("Submit request"):
            request_id = "REQ-" + uuid.uuid4().hex[:10]
            mom_path = ""
            if mom_file is not None:
                os.makedirs(config.UPLOADS_DIR, exist_ok=True)
                mom_path = os.path.join(config.UPLOADS_DIR, f"{request_id}_{mom_file.name}")
                with open(mom_path, "wb") as f:
                    f.write(mom_file.getbuffer())

            requests_data = load_requests()
            requests_data.append({
                "id": request_id,
                "meetingType": meeting_type,
                "quarter": quarter,
                "year": year,
                "previousMoMPath": mom_path,
                "status": STATUS_NEW,
                "paContactedAt": None,
                "confirmedDate": None,
                "confirmedTime": None,
                "confirmedEndTime": None,
                "noticeSentAt": None,
                "agendaDeadline": None,
                "agendaSubmissions": [],
                "mode": "manual" if mode_choice.startswith("Manual") else "auto",
                "customSubject": None,
                "customBody": None,
                "log": [{"ts": datetime.now().isoformat(timespec="seconds"),
                          "note": "Request submitted via Streamlit app."}],
                "createdAt": datetime.now().isoformat(timespec="seconds")
            })
            save_requests(requests_data)
            st.session_state["last_submitted_id"] = request_id
            st.toast(f"Request {request_id} submitted!", icon="✅")
            st.rerun()

# ==================== MeetingMaster Admin ====================
with tab_admin:
    st.subheader("MeetingMaster list")

    admin_locked = bool(config.ADMIN_PASSWORD) and not st.session_state.get("admin_authed")

    if admin_locked:
        st.caption("This tab is password-protected.")
        pw = st.text_input("Admin password", type="password", key="admin_pw_input")
        if st.button("Unlock"):
            if pw == config.ADMIN_PASSWORD:
                st.session_state["admin_authed"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    else:
        if config.ADMIN_PASSWORD and st.button("🔒 Lock this tab"):
            st.session_state["admin_authed"] = False
            st.rerun()

        st.caption("One entry per meeting type — who chairs it, who the PA is, who attends.")

        meetingmaster = load_meetingmaster()

        for i, m in enumerate(meetingmaster):
            with st.expander(m["meetingType"]):
                m["chairmanName"] = st.text_input("Chairman name", m.get("chairmanName", ""), key=f"cn{i}")
                m["chairmanEmail"] = st.text_input("Chairman email", m.get("chairmanEmail", ""), key=f"ce{i}")
                m["paName"] = st.text_input("PA name", m.get("paName", ""), key=f"pn{i}")
                m["paEmail"] = st.text_input("PA email", m.get("paEmail", ""), key=f"pe{i}")
                attendees_text = "\n".join(f"{a['name']}, {a['email']}" for a in m.get("attendees", []))
                attendees_text = st.text_area("Attendees (one per line: name, email)", attendees_text, key=f"at{i}")
                m["attendees"], skipped = parse_attendees_text(attendees_text)
                if m["attendees"]:
                    st.caption(f"✅ Recognized {len(m['attendees'])} attendee(s): " +
                               ", ".join(a["name"] for a in m["attendees"]))
                if skipped:
                    st.warning("Couldn't parse these lines (need 'Name, email@example.com'): " + " | ".join(skipped))
                c1, c2 = st.columns(2)
                if c1.button("Save", key=f"save{i}"):
                    save_meetingmaster(meetingmaster)
                    st.success("Saved.")
                if c2.button("Delete", key=f"del{i}"):
                    meetingmaster.pop(i)
                    save_meetingmaster(meetingmaster)
                    st.rerun()

        st.divider()
        st.markdown("**Add a new meeting type**")
        with st.form("add_type_form", clear_on_submit=True):
            new_name = st.text_input("Meeting type name")
            c1, c2 = st.columns(2)
            chairman_name = c1.text_input("Chairman name")
            chairman_email = c2.text_input("Chairman email")
            c3, c4 = st.columns(2)
            pa_name = c3.text_input("PA name")
            pa_email = c4.text_input("PA email")
            attendees_raw = st.text_area("Attendees (one per line: name, email)")
            add_submitted = st.form_submit_button("Add meeting type")

            if add_submitted and new_name:
                attendees, skipped = parse_attendees_text(attendees_raw)
                if skipped:
                    st.warning("Couldn't parse these attendee lines (need 'Name, email@example.com'), so they were skipped: " + " | ".join(skipped))
                meetingmaster.append({
                    "id": "MT-" + uuid.uuid4().hex[:8],
                    "meetingType": new_name,
                    "chairmanName": chairman_name,
                    "chairmanEmail": chairman_email,
                    "paName": pa_name,
                    "paEmail": pa_email,
                    "attendees": attendees,
                    "notes": ""
                })
                save_meetingmaster(meetingmaster)
                st.success(f"Added '{new_name}'.")
                st.rerun()

# ==================== Agent Dashboard ====================
with tab_dashboard:
    st.subheader("Agent dashboard")
    mode_label = "🟢 LIVE — real emails send" if config.LIVE_MODE else "🟡 TEST MODE — nothing actually sends"
    st.caption(f"Current mode: **{mode_label}** (change LIVE_MODE in your .env file)")

    all_requests = load_requests()
    counts = {
        STATUS_NEW: sum(1 for r in all_requests if r["status"] == STATUS_NEW),
        STATUS_AWAITING_PA: sum(1 for r in all_requests if r["status"] == STATUS_AWAITING_PA),
        STATUS_DATE_CONFIRMED: sum(1 for r in all_requests if r["status"] == STATUS_DATE_CONFIRMED),
        STATUS_COLLECTING_AGENDA: sum(1 for r in all_requests if r["status"] == STATUS_COLLECTING_AGENDA),
        STATUS_AGENDA_COMPILED: sum(1 for r in all_requests if r["status"] == STATUS_AGENDA_COMPILED),
    }
    legacy_notice_sent = sum(1 for r in all_requests if r["status"] == STATUS_NOTICE_SENT)

    row1 = st.columns(3)
    row1[0].metric("🆕 New", counts[STATUS_NEW])
    row1[1].metric("⏳ Awaiting PA", counts[STATUS_AWAITING_PA])
    row1[2].metric("📅 Date Confirmed", counts[STATUS_DATE_CONFIRMED])
    row2 = st.columns(2)
    row2[0].metric("📝 Collecting Agenda", counts[STATUS_COLLECTING_AGENDA])
    row2[1].metric("✅ Agenda Compiled", counts[STATUS_AGENDA_COMPILED])
    if legacy_notice_sent:
        st.caption(f"({legacy_notice_sent} older request(s) still at the pre-agenda-tracking \"Notice Sent\" status)")

    st.caption("Each request has its own Run/Review button below — process them individually, in any order. "
               "For unattended background checking, run `python run_agent_loop.py` in a separate terminal.")

    st.divider()

    @st.fragment(run_every=3)
    def render_requests():
        requests_data = load_requests()
        if not requests_data:
            st.info("No requests yet.")
            return

        for req in reversed(requests_data):
            mt = get_meetingmaster_by_type(req["meetingType"])
            req_mode = req.get("mode", "auto")
            review_key = f"reviewing_{req['id']}"
            editing_now = req_mode == "manual" and req["status"] in (STATUS_NEW, STATUS_DATE_CONFIRMED) \
                and st.session_state.get(review_key)

            with st.container(border=True):
                col1, col2, col3 = st.columns([5, 2, 1])
                with col1:
                    st.markdown(f"**{req['meetingType']}** · {req['quarter']} {req['year']} · `{req['id']}`")
                    status_line = f"Status: **{req['status']}**"
                    if req.get("confirmedDate"):
                        status_line += f" · Confirmed: {req['confirmedDate']} {req.get('confirmedTime', '')}"
                    mode_tag = "✋ Manual" if req_mode == "manual" else "⚙️ Auto"
                    st.markdown(f"{status_line} · {mode_tag}")
                with col2:
                    if req["status"] in (STATUS_NOTICE_SENT, STATUS_AGENDA_COMPILED):
                        st.caption("✅ Complete")
                    elif req["status"] == STATUS_AWAITING_PA:
                        if st.button("▶ Check reply", key=f"run{req['id']}", use_container_width=True):
                            process_single_request(creds, req["id"])
                            st.rerun()
                    elif req["status"] == STATUS_COLLECTING_AGENDA:
                        if st.button("▶ Check replies", key=f"run{req['id']}", use_container_width=True):
                            process_single_request(creds, req["id"])
                            st.rerun()
                    elif req_mode == "auto":
                        if st.button("▶ Run", key=f"run{req['id']}", use_container_width=True):
                            process_single_request(creds, req["id"])
                            st.rerun()
                    elif not editing_now:
                        if st.button("✏️ Review", key=f"run{req['id']}", use_container_width=True):
                            st.session_state[review_key] = True
                            st.rerun()
                with col3:
                    if st.button("🗑", key=f"delreq{req['id']}", use_container_width=True):
                        requests_data = [r for r in requests_data if r["id"] != req["id"]]
                        save_requests(requests_data)
                        st.rerun()

                if editing_now:
                    if not mt:
                        st.warning("No MeetingMaster entry found for this meeting type — fix that in Admin first.")
                    else:
                        default_subject, default_body = (
                            render_default_availability_email(mt, req["quarter"], req["year"])
                            if req["status"] == STATUS_NEW
                            else render_default_notice_email(req, mt)
                        )
                        # setdefault (not a plain assign) so a fragment auto-rerun
                        # (this whole panel refreshes every 3s) never clobbers text
                        # the user is mid-editing — it only seeds when truly unset.
                        st.session_state.setdefault(f"subj_{req['id']}", req.get("customSubject") or default_subject)
                        st.session_state.setdefault(f"body_{req['id']}", req.get("customBody") or default_body)
                        st.text_input("Subject", key=f"subj_{req['id']}")
                        st.text_area("Body", key=f"body_{req['id']}", height=220)
                        sc1, sc2 = st.columns(2)
                        if sc1.button("✅ Confirm & Send", key=f"send{req['id']}", type="primary", use_container_width=True):
                            process_single_request(
                                creds, req["id"], force_send=True,
                                override_subject=st.session_state[f"subj_{req['id']}"],
                                override_body=st.session_state[f"body_{req['id']}"],
                            )
                            st.session_state[review_key] = False
                            st.session_state.pop(f"subj_{req['id']}", None)
                            st.session_state.pop(f"body_{req['id']}", None)
                            st.rerun()
                        if sc2.button("Cancel", key=f"cancelreview{req['id']}", use_container_width=True):
                            st.session_state[review_key] = False
                            st.session_state.pop(f"subj_{req['id']}", None)
                            st.session_state.pop(f"body_{req['id']}", None)
                            st.rerun()

                if req["status"] == STATUS_COLLECTING_AGENDA:
                    st.divider()
                    n_submissions = len(req.get("agendaSubmissions", []))
                    st.caption(f"📥 {n_submissions} agenda item(s) collected so far.")
                    current_deadline = req.get("agendaDeadline")
                    deadline_default = (
                        datetime.strptime(current_deadline, "%Y-%m-%d").date()
                        if current_deadline else datetime.now().date()
                    )
                    dcol1, dcol2 = st.columns([2, 1])
                    with dcol1:
                        new_deadline = st.date_input("Agenda submission deadline",
                                                      value=deadline_default, key=f"deadline_{req['id']}")
                    with dcol2:
                        st.write("")
                        if st.button("📋 Compile & Create Draft", key=f"compile{req['id']}",
                                     type="primary", use_container_width=True):
                            trigger_agenda_compile(creds, req["id"])
                            st.rerun()
                    new_deadline_str = new_deadline.strftime("%Y-%m-%d")
                    if new_deadline_str != current_deadline:
                        fresh = load_requests()
                        for r in fresh:
                            if r["id"] == req["id"]:
                                r["agendaDeadline"] = new_deadline_str
                        save_requests(fresh)
                elif req["status"] == STATUS_AGENDA_COMPILED:
                    st.info("📬 The compiled agenda has been saved as a draft in your Gmail Drafts folder — "
                            "review and send it from there.")

                with st.expander("Agent log"):
                    for entry in req.get("log", []):
                        st.text(f"[{entry['ts']}] {entry['note']}")

    render_requests()
