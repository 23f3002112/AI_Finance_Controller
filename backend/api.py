import os
import csv
import json
import uuid
import shutil
import asyncio
from fastapi import FastAPI, File, UploadFile, BackgroundTasks, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
from typing import Optional

from agent.orchestrator import run_orchestrator

app = FastAPI(title="AI Finance Controller API")

@app.get("/")
async def root():
    return {"status": "AI Finance Controller API is running perfectly! 🚀"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Open for local dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RUNS_DIR = os.path.join(os.path.dirname(__file__), "runs")
os.makedirs(RUNS_DIR, exist_ok=True)

EXPECTED_GATEWAY_COLS = {"transaction_id", "amount", "timestamp", "status", "utr"}
EXPECTED_BANK_COLS = {"bank_ref", "amount", "value_date", "narration"}

def validate_csv_columns(file_path: str, expected_cols: set, label: str):
    try:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader, [])
            headers = set([h.strip() for h in headers])
            missing = expected_cols - headers
            if missing:
                return False, f"{label} CSV is missing required columns: {missing}. Found: {headers}"
        return True, ""
    except Exception as e:
        return False, f"Could not read {label} CSV: {str(e)}"

@app.post("/upload")
async def upload_files(gateway_csv: UploadFile = File(...), bank_csv: UploadFile = File(...)):
    run_id = str(uuid.uuid4())
    run_dir = os.path.join(RUNS_DIR, run_id)
    os.makedirs(run_dir, exist_ok=True)
    
    gw_path = os.path.join(run_dir, "gateway.csv")
    bank_path = os.path.join(run_dir, "bank.csv")
    
    with open(gw_path, "wb") as f:
        shutil.copyfileobj(gateway_csv.file, f)
    with open(bank_path, "wb") as f:
        shutil.copyfileobj(bank_csv.file, f)
        
    gw_valid, gw_msg = validate_csv_columns(gw_path, EXPECTED_GATEWAY_COLS, "Gateway")
    if not gw_valid:
        shutil.rmtree(run_dir)
        raise HTTPException(status_code=400, detail=gw_msg)
        
    bank_valid, bank_msg = validate_csv_columns(bank_path, EXPECTED_BANK_COLS, "Bank")
    if not bank_valid:
        shutil.rmtree(run_dir)
        raise HTTPException(status_code=400, detail=bank_msg)
        
    status_path = os.path.join(run_dir, "status.json")
    with open(status_path, "w") as f:
        json.dump({"status": "pending"}, f)
        
    return {"run_id": run_id, "message": "Files uploaded successfully."}


def execute_orchestrator_task(run_id: str, gw_path: str, bank_path: str, out_path: str, status_path: str):
    with open(status_path, "w") as f:
        json.dump({"status": "running"}, f)
    try:
        # Run the agent pipeline
        run_orchestrator(gw_path, bank_path, out_path, workers=4)
        with open(status_path, "w") as f:
            json.dump({"status": "completed"}, f)
    except Exception as e:
        with open(status_path, "w") as f:
            json.dump({"status": "failed", "error": str(e)}, f)

@app.post("/run/{run_id}")
async def run_reconciliation(run_id: str, background_tasks: BackgroundTasks):
    run_dir = os.path.join(RUNS_DIR, run_id)
    if not os.path.exists(run_dir):
        raise HTTPException(status_code=404, detail="Run ID not found")
        
    gw_path = os.path.join(run_dir, "gateway.csv")
    bank_path = os.path.join(run_dir, "bank.csv")
    out_path = os.path.join(run_dir, "reconciliation_report.json")
    status_path = os.path.join(run_dir, "status.json")
    
    with open(status_path, "r") as f:
        status_data = json.load(f)
        
    if status_data.get("status") == "running":
        return {"run_id": run_id, "status": "running"}
        
    background_tasks.add_task(
        execute_orchestrator_task,
        run_id, gw_path, bank_path, out_path, status_path
    )
    
    return {"run_id": run_id, "status": "running", "message": "Reconciliation started in the background."}

@app.get("/status/{run_id}")
async def get_status(run_id: str):
    status_path = os.path.join(RUNS_DIR, run_id, "status.json")
    if not os.path.exists(status_path):
        raise HTTPException(status_code=404, detail="Run ID not found")
    with open(status_path, "r") as f:
        return json.load(f)

@app.get("/results/{run_id}")
async def get_results(run_id: str):
    report_path = os.path.join(RUNS_DIR, run_id, "reconciliation_report.json")
    if not os.path.exists(report_path):
        raise HTTPException(status_code=404, detail="Results not found. Job may still be running or failed.")
        
    with open(report_path, "r") as f:
        data = json.load(f)
        
    return {
        "run_metadata": data.get("run_metadata", {}),
        "summary": data.get("summary", {})
    }

@app.get("/exceptions/{run_id}")
async def get_exceptions(
    run_id: str, 
    reason: Optional[str] = None, 
    page: int = Query(1, ge=1), 
    limit: int = Query(50, ge=1, le=100)
):
    report_path = os.path.join(RUNS_DIR, run_id, "reconciliation_report.json")
    if not os.path.exists(report_path):
        raise HTTPException(status_code=404, detail="Results not found.")
        
    with open(report_path, "r") as f:
        data = json.load(f)
        
    gw_ex = data.get("gateway_exceptions", [])
    bank_ex = data.get("bank_exceptions", [])
    
    # Standardize output for the frontend
    exceptions = []
    for ex in gw_ex:
        exceptions.append({
            "type": "gateway",
            "id": ex["transaction_id"],
            "reason": ex["reason"],
            "details": ex["details"]
        })
    for ex in bank_ex:
        exceptions.append({
            "type": "bank",
            "id": ex["bank_ref"],
            "reason": ex["classification"],
            "details": ex["reasoning"]
        })
        
    if reason:
        exceptions = [ex for ex in exceptions if ex["reason"] == reason]
        
    total = len(exceptions)
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "exceptions": exceptions[start_idx:end_idx]
    }

@app.get("/audit/{run_id}/{transaction_id}")
async def get_audit(run_id: str, transaction_id: str):
    report_path = os.path.join(RUNS_DIR, run_id, "reconciliation_report.json")
    if not os.path.exists(report_path):
        raise HTTPException(status_code=404, detail="Results not found.")
        
    with open(report_path, "r") as f:
        data = json.load(f)
        
    # Search in transactions
    for txn in data.get("transactions", []):
        if txn.get("transaction_id") == transaction_id:
            return {"transaction_id": transaction_id, "trace": txn.get("trace", [])}
            
    # Search in gateway exceptions
    for txn in data.get("gateway_exceptions", []):
        if txn.get("transaction_id") == transaction_id:
            return {"transaction_id": transaction_id, "trace": txn.get("trace", [])}
            
    # Search in bank exceptions
    for txn in data.get("bank_exceptions", []):
        if txn.get("bank_ref") == transaction_id:
            return {"transaction_id": transaction_id, "trace": txn.get("trace", [])}
            
    raise HTTPException(status_code=404, detail="Transaction ID not found in report.")
