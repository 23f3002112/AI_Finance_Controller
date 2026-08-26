import streamlit as st
import requests
import pandas as pd
import time
import json
import os

API_URL = os.getenv("API_URL", "https://ai-finance-controller-wfym.onrender.com")

st.set_page_config(page_title="AI Finance Controller", page_icon="💸", layout="wide")

st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    h1 { color: #1e3a8a; font-family: 'Inter', sans-serif; font-weight: 700; }
    h2, h3 { color: #3b82f6; font-family: 'Inter', sans-serif; }
    .stButton>button { background-color: #2563eb; color: white; border-radius: 6px; padding: 0.5rem 2rem; font-weight: 600; }
    .stButton>button:hover { background-color: #1d4ed8; color: white; }
    .metric-card { background: white; padding: 1.5rem; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); text-align: center; }
    .metric-value { font-size: 2.5rem; font-weight: bold; color: #1e40af; }
    .metric-label { font-size: 1rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; }
    </style>
""", unsafe_allow_html=True)

st.sidebar.title("💸 AI Controller")
st.sidebar.markdown("Enterprise Reconciliation Engine")
st.sidebar.divider()
view = st.sidebar.radio("Navigation", ["▶ Run Engine", "📊 Summary Dashboard", "🔍 Exceptions & Audit"])

if "run_id" not in st.session_state:
    st.session_state.run_id = None

if view == "▶ Run Engine":
    st.title("Reconciliation Engine")
    st.markdown("Upload your payment gateway ledger and bank statement to begin the AI-driven reconciliation process.")
    
    col1, col2 = st.columns(2)
    with col1:
        gw_file = st.file_uploader("Upload Gateway Ledger (CSV)", type=["csv"])
    with col2:
        bank_file = st.file_uploader("Upload Bank Statement (CSV)", type=["csv"])
    
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🚀 Start Reconciliation"):
        if gw_file and bank_file:
            with st.spinner("Processing files and analyzing narrations..."):
                files = {
                    "gateway_csv": (gw_file.name, gw_file.getvalue(), "text/csv"),
                    "bank_csv": (bank_file.name, bank_file.getvalue(), "text/csv")
                }
                try:
                    res = requests.post(f"{API_URL}/upload", files=files)
                    if res.status_code == 200:
                        run_id = res.json()["run_id"]
                        st.session_state.run_id = run_id
                        
                        run_res = requests.post(f"{API_URL}/run/{run_id}")
                        if run_res.status_code == 200:
                            status_placeholder = st.empty()
                            progress_bar = st.progress(0)
                            
                            while True:
                                stat_res = requests.get(f"{API_URL}/status/{run_id}")
                                if stat_res.status_code == 200:
                                    status = stat_res.json().get("status")
                                    status_placeholder.info(f"Engine Status: **{status.upper()}**")
                                    
                                    if status == "completed":
                                        progress_bar.progress(100)
                                        st.success("Reconciliation Completed Successfully!")
                                        break
                                    elif status == "failed":
                                        progress_bar.empty()
                                        st.error(f"Pipeline failed: {stat_res.json().get('error')}")
                                        break
                                    else:
                                        progress_bar.progress(50)
                                time.sleep(1.5)
                        else:
                            st.error(f"Failed to start run: {run_res.text}")
                    else:
                        st.error(f"Upload failed: {res.text}")
                except Exception as e:
                    st.error(f"Connection error: {str(e)}. Ensure the backend API is running.")
        else:
            st.warning("Please upload both Gateway and Bank CSV files to proceed.")

elif view == "📊 Summary Dashboard":
    st.title("Executive Summary")
    if not st.session_state.run_id:
        st.info("No active run found. Please navigate to 'Run Engine' to start a new reconciliation job.")
    else:
        try:
            res = requests.get(f"{API_URL}/results/{st.session_state.run_id}")
            if res.status_code == 200:
                data = res.json()
                summary = data.get("summary", {})
                
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown(f'<div class="metric-card"><div class="metric-value">{summary.get("match_rate", 0)}%</div><div class="metric-label">Match Rate</div></div>', unsafe_allow_html=True)
                with c2:
                    st.markdown(f'<div class="metric-card"><div class="metric-value">{summary.get("precision", 0)}%</div><div class="metric-label">Precision Score</div></div>', unsafe_allow_html=True)
                with c3:
                    st.markdown(f'<div class="metric-card"><div class="metric-value">{data.get("run_metadata", {}).get("total_records", 0)}</div><div class="metric-label">Total Records Processed</div></div>', unsafe_allow_html=True)
                
                st.markdown("<br><br>", unsafe_allow_html=True)
                st.subheader("Resolution Breakdown")
                
                bmt = summary.get("by_match_type", {})
                if bmt:
                    df_chart = pd.DataFrame([{"Match Type": k.replace("_", " ").title(), "Volume": v["count"]} for k, v in bmt.items()]).set_index("Match Type")
                    st.bar_chart(df_chart, use_container_width=True, height=400)
            else:
                st.error("Results not ready or run failed.")
        except Exception as e:
            st.error(f"Failed to fetch summary: {str(e)}")

elif view == "🔍 Exceptions & Audit":
    st.title("Exceptions Control Center")
    if not st.session_state.run_id:
        st.info("No active run found. Please navigate to 'Run Engine' to start a new reconciliation job.")
    else:
        try:
            res = requests.get(f"{API_URL}/exceptions/{st.session_state.run_id}?limit=100")
            if res.status_code == 200:
                exceptions = res.json().get("exceptions", [])
                
                if exceptions:
                    df_ex = pd.DataFrame(exceptions)
                    df_ex.rename(columns={"type": "Source", "id": "Reference ID", "reason": "Classification", "details": "AI Reasoning"}, inplace=True)
                    
                    reasons = ["All Classifications"] + list(df_ex["Classification"].unique())
                    selected_reason = st.selectbox("Filter Exceptions", reasons)
                    
                    if selected_reason != "All Classifications":
                        df_ex = df_ex[df_ex["Classification"] == selected_reason]
                        
                    st.dataframe(df_ex, use_container_width=True, hide_index=True)
                    
                    st.divider()
                    st.subheader("Deep Dive: AI Audit Trail")
                    st.markdown("Enter a Transaction ID or Bank Ref to view the step-by-step reasoning behind its resolution.")
                    lookup_id = st.text_input("Reference ID Lookup", placeholder="e.g., pay_1234 or BNK9001")
                    
                    if lookup_id:
                        audit_res = requests.get(f"{API_URL}/audit/{st.session_state.run_id}/{lookup_id}")
                        if audit_res.status_code == 200:
                            trace = audit_res.json().get("trace", [])
                            st.markdown(f"### Audit Trace for `{lookup_id}`")
                            for step in trace:
                                status_color = "green" if step["result"] in ["exact_match", "llm_match", "bank_fee"] else "orange" if "escalate" in step["result"] else "gray"
                                with st.expander(f"**{step['state']}** ➔ {step['result']}", expanded=True):
                                    st.caption(f"Timestamp: {step['timestamp']}")
                                    if step['details']:
                                        st.write(f"**AI Notes:** {step['details']}")
                        else:
                            st.warning(f"No audit trace found for '{lookup_id}'. Please check the ID and try again.")
                else:
                    st.success("🎉 No exceptions found! 100% Reconciliation achieved.")
            else:
                st.error("Failed to fetch exceptions.")
        except Exception as e:
            st.error(f"Failed to fetch exceptions: {str(e)}")
