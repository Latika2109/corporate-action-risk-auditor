"""Phase 4: Streamlit Exception Dashboard.

Single-page app: Upload & Run -> Exception Queue -> Human Sign-Off.
Talks to the FastAPI backend over HTTP; no direct DB access.
"""
from __future__ import annotations

import os

import pandas as pd
import requests
import streamlit as st

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")
if not API_BASE.startswith(("http://", "https://")):
    # Render's `fromService: host` gives a bare private-network hostname
    # with no scheme (e.g. "risk-auditor-backend"); the private network
    # is plain HTTP.
    API_BASE = f"http://{API_BASE}"

st.set_page_config(page_title="Corporate Action Risk Auditor", layout="wide")
st.title("Corporate Action Risk Auditor")
st.caption("Human-in-the-loop exception management for cross-exchange corporate actions")

tab_upload, tab_exceptions, tab_signoff = st.tabs(
    ["Upload & Run", "Exception Queue", "Sign-Off"]
)

# ---------------------------------------------------------------------------
# Tab 1: Upload & Run
# ---------------------------------------------------------------------------
with tab_upload:
    st.subheader("Data Ingestion Hub")
    col1, col2 = st.columns(2)
    with col1:
        nse_file = st.file_uploader("NSE raw file (.csv or .txt)", key="nse")
    with col2:
        bse_file = st.file_uploader("BSE raw file (.csv or .txt)", key="bse")

    if st.button("Run Validation Pipeline", type="primary", disabled=not (nse_file and bse_file)):
        with st.spinner("Ingesting, normalizing, and cross-validating..."):
            files = {
                "nse_file": (nse_file.name, nse_file.getvalue()),
                "bse_file": (bse_file.name, bse_file.getvalue()),
            }
            try:
                resp = requests.post(f"{API_BASE}/api/ingest/run", files=files, timeout=60)
                resp.raise_for_status()
                result = resp.json()
                st.success(
                    f"Processed {result['total_rows_seen']} rows -> "
                    f"{len(result['verified_events'])} verified, "
                    f"{len(result['anomalies'])} anomalies, "
                    f"{len(result['quarantined'])} quarantined"
                )
                st.session_state["last_run"] = result
            except requests.RequestException as exc:
                st.error(f"Pipeline call failed: {exc}")

    if "last_run" in st.session_state:
        st.subheader("Last Run Summary")
        st.json(st.session_state["last_run"], expanded=False)

# ---------------------------------------------------------------------------
# Tab 2: Exception Queue
# ---------------------------------------------------------------------------
with tab_exceptions:
    st.subheader("Quarantine Queue")
    st.caption("Records that failed schema ingestion entirely")
    try:
        quarantine = requests.get(f"{API_BASE}/api/quarantine", timeout=15).json()
        if quarantine:
            st.dataframe(pd.DataFrame(quarantine), use_container_width=True)
        else:
            st.info("No quarantined records.")
    except requests.RequestException as exc:
        st.error(f"Could not load quarantine queue: {exc}")

    st.subheader("Discrepancy Matrix")
    st.caption("ISINs with cross-exchange date or ratio conflicts")
    try:
        anomalies = requests.get(f"{API_BASE}/api/anomalies", timeout=15).json()
        if anomalies:
            df = pd.DataFrame(anomalies)
            st.dataframe(
                df[df["resolution_status"] == "REQUIRES_HUMAN_REVIEW"],
                use_container_width=True,
            )
        else:
            st.info("No anomalies detected.")
    except requests.RequestException as exc:
        st.error(f"Could not load anomalies: {exc}")

    st.subheader("All Ingested Events")
    try:
        events = requests.get(f"{API_BASE}/api/events", timeout=15).json()
        if events:
            st.dataframe(pd.DataFrame(events), use_container_width=True)
        else:
            st.info("No events ingested yet.")
    except requests.RequestException as exc:
        st.error(f"Could not load events: {exc}")

# ---------------------------------------------------------------------------
# Tab 3: Simulation Viewer & Sign-Off
# ---------------------------------------------------------------------------
with tab_signoff:
    st.subheader("Trigger Simulation")
    try:
        events = requests.get(f"{API_BASE}/api/events", timeout=15).json()
        verified_events = [e for e in events if e["status"] == "VERIFIED"]
    except requests.RequestException as exc:
        verified_events = []
        st.error(f"Could not load events: {exc}")

    if verified_events:
        options = {f"{e['isin']} ({e['action_type']} x{e['ratio_multiplier']})": e["event_id"] for e in verified_events}
        choice = st.selectbox("Select a VERIFIED event to simulate", list(options.keys()))
        if st.button("Run Shadow Simulation"):
            event_id = options[choice]
            try:
                resp = requests.post(f"{API_BASE}/api/simulate/event/{event_id}", timeout=30)
                resp.raise_for_status()
                st.success(f"Simulated {len(resp.json())} client holdings.")
            except requests.RequestException as exc:
                st.error(f"Simulation failed: {exc}")
    else:
        st.info("No VERIFIED events available to simulate yet.")

    st.divider()
    st.subheader("Human-in-the-Loop Sign Off")
    st.caption("Diff view: current holdings vs. simulated post-event holdings")

    try:
        pending = requests.get(f"{API_BASE}/api/simulate/pending", timeout=15).json()
    except requests.RequestException as exc:
        pending = []
        st.error(f"Could not load pending simulations: {exc}")

    if not pending:
        st.info("No simulations pending authorization.")
    else:
        for entry in pending:
            with st.container(border=True):
                cols = st.columns([2, 2, 2, 2, 2])
                cols[0].metric("Client", entry["client_id"])
                cols[1].metric("ISIN", entry["isin"])
                cols[2].metric("Pre-Event Shares", entry["pre_event_shares"])
                cols[3].metric("Simulated Post-Event", entry["simulated_post_event_shares"])
                variance = entry.get("variance", 0)
                cols[4].metric("Variance", variance, delta=variance if variance else None)

                approve_col, reject_col = st.columns(2)
                if approve_col.button("Approve Ledger Correction", key=f"approve_{entry['simulation_id']}"):
                    requests.post(
                        f"{API_BASE}/api/simulate/signoff",
                        json={
                            "simulation_id": entry["simulation_id"],
                            "approver": "risk_officer",
                            "decision": "APPROVED",
                        },
                        timeout=15,
                    )
                    st.rerun()
                if reject_col.button("Reject", key=f"reject_{entry['simulation_id']}"):
                    requests.post(
                        f"{API_BASE}/api/simulate/signoff",
                        json={
                            "simulation_id": entry["simulation_id"],
                            "approver": "risk_officer",
                            "decision": "REJECTED",
                        },
                        timeout=15,
                    )
                    st.rerun()
