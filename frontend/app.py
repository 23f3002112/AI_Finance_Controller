import streamlit as st
import requests
import pandas as pd
import time
import json

API_URL = "http://localhost:8000"

st.set_page_config(page_title="AI Finance Controller", layout="wide")

st.sidebar.title("Navigation")
view = st.sidebar.radio("Go to", ["Run", "Summary", "Exceptions & Audit"])

# Use session state to persist run_id across views
if "run_id" not in st.session_state:
    st.session_state.run_id = None

if view == "Run":
    st.title("Run Reconciliation Pipeline")
    
    gw_file = st.file_uploader("Upload Gateway Ledger CSV", type=["csv"])
    bank_file = st.file_uploader("Upload Bank Statement CSV", type=["csv"])
    
    if st.button("Run Reconciliation"):
        if gw_file and bank_file:
            with st.spinner("Uploading files..."):
                files = {
                    "gateway_csv": (gw_file.name, gw_file.getvalue(), "text/csv"),
                    "bank_csv": (bank_file.name, bank_file.getvalue(), "text/csv")
                }
                try:
                    res = requests.post(f"{API_URL}/upload", files=files)
                    if res.status_code == 200:
                        run_id = res.json()["run_id"]
                        st.session_state.run_id = run_id
                        st.success(f"Files uploaded! Run ID: {run_id}")
                        
                        # Trigger run
                        run_res = requests.post(f"{API_URL}/run/{run_id}")
                        if run_res.status_code == 200:
                            st.info("Reconciliation started. Polling status...")
                            
                            status_placeholder = st.empty()
                            while True:
                                stat_res = requests.get(f"{API_URL}/status/{run_id}")
                                if stat_res.status_code == 200:
                                    status = stat_res.json().get("status")
                                    status_placeholder.write(f"Current Status: **{status.upper()}**")
                                    if status in ["completed", "failed"]:
                                        if status == "completed":
                                            st.success("Reconciliation Completed! Go to Summary tab.")
                                        else:
                                            st.error(f"Pipeline failed: {stat_res.json().get('error')}")
                                        break
                                time.sleep(1)
                        else:
                            st.error(f"Failed to start run: {run_res.text}")
                    else:
                        st.error(f"Upload failed: {res.text}")
                except Exception as e:
                    st.error(f"Connection error: {str(e)}. Is the API running?")
        else:
            st.warning("Please upload both CSV files.")

elif view == "Summary":
    st.title("Run Summary")
    if not st.session_state.run_id:
        st.warning("No run active. Please go to 'Run' and start a job first.")
    else:
        try:
            res = requests.get(f"{API_URL}/results/{st.session_state.run_id}")
            if res.status_code == 200:
                data = res.json()
                summary = data.get("summary", {})
                meta = data.get("run_metadata", {})
                
                col1, col2, col3 = st.columns(3)
                col1.metric("Match Rate", f"{summary.get('match_rate', 0)}%")
                col2.metric("Precision", f"{summary.get('precision', 0)}%")
                col3.metric("Total Records", meta.get('total_records', 0))
                
                bmt = summary.get("by_match_type", {})
                if bmt:
                    st.subheader("Matches by Type")
                    df_chart = pd.DataFrame([
                        {"Match Type": k, "Count": v["count"]} 
                        for k, v in bmt.items()
                    ]).set_index("Match Type")
                    st.bar_chart(df_chart)
            else:
                st.error("Results not ready or run failed.")
        except Exception as e:
            st.error(f"Failed to fetch summary: {str(e)}")

elif view == "Exceptions & Audit":
    st.title("Exceptions & Audit Trace")
    if not st.session_state.run_id:
        st.warning("No run active. Please go to 'Run' and start a job first.")
    else:
        # Fetch exceptions
        try:
            res = requests.get(f"{API_URL}/exceptions/{st.session_state.run_id}?limit=100")
            if res.status_code == 200:
                exceptions = res.json().get("exceptions", [])
                
                if exceptions:
                    df_ex = pd.DataFrame(exceptions)
                    reasons = ["All"] + list(df_ex["reason"].unique())
                    selected_reason = st.selectbox("Filter by Reason Code", reasons)
                    
                    if selected_reason != "All":
                        df_ex = df_ex[df_ex["reason"] == selected_reason]
                        
                    st.dataframe(df_ex, use_container_width=True)
                    
                    st.divider()
                    st.subheader("Audit Trace Lookup")
                    lookup_id = st.text_input("Enter Transaction ID / Bank Ref to view audit trace:", placeholder="e.g., pay_1234 or BNK9001")
                    
                    if lookup_id:
                        audit_res = requests.get(f"{API_URL}/audit/{st.session_state.run_id}/{lookup_id}")
                        if audit_res.status_code == 200:
                            trace = audit_res.json().get("trace", [])
                            st.write(f"Trace for **{lookup_id}**:")
                            for step in trace:
                                with st.expander(f"{step['state']} -> {step['result']}", expanded=True):
                                    st.write(f"**Timestamp:** {step['timestamp']}")
                                    st.write(f"**Details:** {step['details']}")
                        else:
                            st.error(f"Trace not found for {lookup_id}.")
                else:
                    st.info("No exceptions found!")
            else:
                st.error("Failed to fetch exceptions.")
        except Exception as e:
            st.error(f"Failed to fetch exceptions: {str(e)}")
