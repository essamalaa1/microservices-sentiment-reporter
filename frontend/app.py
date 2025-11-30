import streamlit as st
import pandas as pd
import requests
import time
import os

# --- Configuration ---
# The internal docker network address for the backend
API_URL = os.getenv("API_URL", "http://backend:8000")
REFRESH_INTERVAL = 10 

st.set_page_config(page_title="Microservices Sentiment Reporter", layout="wide")

# --- Session State ---
if "reports" not in st.session_state:
    st.session_state.reports = []
if "is_running" not in st.session_state:
    st.session_state.is_running = False
if "preview_df" not in st.session_state:
    st.session_state.preview_df = None
if "available_cols" not in st.session_state:
    st.session_state.available_cols = []

# --- UI Helpers ---
def get_backend_preview(sheet_id):
    try:
        resp = requests.post(f"{API_URL}/preview", json={"sheet_id": sheet_id}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return pd.DataFrame(data["preview_data"]), data["columns"]
        else:
            st.error(f"Backend Error: {resp.text}")
            return None, []
    except Exception as e:
        st.error(f"Connection Error: {e}")
        return None, []

def process_next_batch(sheet_id, batch_size, cols, model, reset=False):
    payload = {
        "sheet_id": sheet_id,
        "batch_size": batch_size,
        "selected_cols": cols,
        "model_label": model,
        "reset_index": reset
    }
    try:
        # Timeout set to 5 minutes for large models
        resp = requests.post(f"{API_URL}/process-batch", json=payload, timeout=300) 
        return resp.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==========================================
# MAIN PAGE LAYOUT
# ==========================================

st.title("📊 Microservices Customer Insights")

# --- Top Configuration Section ---
with st.container():
    st.subheader("Configuration")
    
    # row 1: Sheet ID and Load Button
    col_input, col_btn = st.columns([3, 1])
    with col_input:
        # Changed: Removed default text (value="")
        sheet_id = st.text_input("Google Sheet ID", value="", placeholder="Enter your Google Sheet ID here...")
    with col_btn:
        st.write("") # Spacer to align button
        st.write("") 
        if st.button("Load Preview", use_container_width=True):
            if sheet_id:
                df, cols = get_backend_preview(sheet_id)
                if df is not None:
                    st.session_state.preview_df = df
                    st.session_state.available_cols = cols
                    st.success("Connected!")
            else:
                st.warning("Please enter a Sheet ID first.")

    # Data Preview Area
    if st.session_state.preview_df is not None:
        st.dataframe(st.session_state.preview_df, height=150, use_container_width=True)
        
        # Row 2: Column Selection
        selected_cols = st.multiselect(
            "Select Columns for AI Analysis", 
            st.session_state.available_cols,
            placeholder="Choose columns containing reviews..."
        )
    else:
        selected_cols = []

    # Row 3: Settings
    col_sett1, col_sett2, col_sett3 = st.columns(3)
    with col_sett1:
        batch_size = st.number_input("Batch Size", min_value=1, value=3)
    with col_sett2:
        model_choice = st.selectbox("AI Model", [
            "LLaMA 3 (8b)", "LLaMA 3.2 (1B)", "DeepSeek R1 (1.5B)"
        ])
    with col_sett3:
        st.write("")
        st.write("")
        reprocess = st.checkbox("Reset / Reprocess All", value=False)

st.divider()

# --- Controls Section ---
ctrl_col1, ctrl_col2 = st.columns(2)
with ctrl_col1:
    if st.button("▶️ Start Reporter", type="primary", use_container_width=True, disabled=st.session_state.is_running):
        if not sheet_id or not selected_cols:
            st.error("Please configure Sheet ID and Columns above.")
        else:
            st.session_state.is_running = True
            if reprocess:
                process_next_batch(sheet_id, batch_size, selected_cols, model_choice, reset=True)
                st.toast("State reset on backend.")
            st.rerun()

with ctrl_col2:
    if st.button("⏹️ Stop Reporter", use_container_width=True, disabled=not st.session_state.is_running):
        st.session_state.is_running = False
        st.rerun()

# --- Status & Output Area ---
status_container = st.empty()

st.subheader("Generated Reports")

# Display reports (Reverse order)
for i, report in enumerate(reversed(st.session_state.reports)):
    with st.expander(f"Report {len(st.session_state.reports) - i}", expanded=(i==0)):
        st.markdown(report)
        
        # PDF Download
        if st.button("📄 Prepare PDF", key=f"pdf_{i}"):
            with st.spinner("Generating PDF via Microservice..."):
                try:
                    pdf_resp = requests.post(f"{API_URL}/generate-pdf", json={"markdown_content": report})
                    if pdf_resp.status_code == 200:
                        st.download_button(
                            "Download PDF", 
                            data=pdf_resp.content, 
                            file_name=f"report_{i}.pdf",
                            mime="application/pdf"
                        )
                    else:
                        st.error("Failed to generate PDF")
                except Exception as e:
                    st.error(f"PDF Error: {e}")

# --- Processing Loop ---
if st.session_state.is_running:
    status_container.info("⏳ Contacting Backend...")
    
    result = process_next_batch(sheet_id, batch_size, selected_cols, model_choice)
    status = result.get("status")
    
    if status == "processed":
        new_report = result.get("report_markdown")
        batch_range = result.get("batch_range")
        st.session_state.reports.append(new_report)
        status_container.success(f"✅ Processed Batch {batch_range}")
        time.sleep(1)
        st.rerun()
        
    elif status == "waiting":
        pending = result.get("rows_pending")
        status_container.warning(f"zzz Waiting for data... ({pending}/{batch_size} new rows). Retrying in {REFRESH_INTERVAL}s.")
        time.sleep(REFRESH_INTERVAL)
        st.rerun()
        
    elif status == "error":
        msg = result.get("message")
        status_container.error(f"❌ Backend Error: {msg}")
        st.session_state.is_running = False