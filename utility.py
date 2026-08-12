"""
Shared Streamlit utility functions.
"""
import hmac

import streamlit as st

import config


def check_password():
    """Gates the whole app behind a single shared password (APP_ACCESS_PASSWORD
    in .env) — separate from, and checked before, the per-user Gmail login.
    Leave APP_ACCESS_PASSWORD blank to leave the app open (matches the
    off-by-default pattern already used for ADMIN_PASSWORD).

    Follows Streamlit's own recommended pattern: the entered password is
    compared with hmac.compare_digest (timing-safe, unlike ==) and never
    kept in session_state past the check — only a boolean 'password_correct'
    flag persists, not the password text itself."""
    if not config.APP_ACCESS_PASSWORD:
        return True

    def password_entered():
        if hmac.compare_digest(st.session_state["password_input"], config.APP_ACCESS_PASSWORD):
            st.session_state["password_correct"] = True
            del st.session_state["password_input"]
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct"):
        return True

    st.text_input("App password", type="password", on_change=password_entered, key="password_input")
    if "password_correct" in st.session_state and not st.session_state["password_correct"]:
        st.error("Incorrect password.")
    return False
