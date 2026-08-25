import os
import json
import argparse
from datetime import datetime
import pandas as pd
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from backend.matcher import load_data, check_exact_utr, check_tolerant_amount_date, check_fuzzy_narration, check_partial_refund
from backend.nlp.llm_matcher import mock_llm_call, call_claude, classify_bank_entry, mock_classify_bank_entry

match_lock = threading.Lock()

class TransactionAgent:
    def __init__(self, txn_dict):
        self.txn = txn_dict
        self.id = txn_dict["transaction_id"]
        self.status = "PENDING"
        self.trace = []
        self.match = None
        self.error = None
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")

    def transition(self, state, result, details=""):
        self.status = state
        self.trace.append({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "state": state,
            "result": result,
            "details": details
        })

    def run(self, bank_df, matched_refs_set):
        try:
            # Deterministic checks can run sequentially inside the thread
            with match_lock:
                cands = bank_df[~bank_df["bank_ref"].isin(matched_refs_set)]
                
            self.transition("EXACT_CHECK", "started")
            res, b, conf = check_exact_utr(self.txn, cands)
            if res == "matched":
                self._claim_match(b, "exact_utr", conf, matched_refs_set)
                return
            self.transition("EXACT_CHECK", res)

            self.transition("TOLERANT_CHECK", "started")
            res, b, conf = check_tolerant_amount_date(self.txn, cands)
            if res == "matched":
                self._claim_match(b, "tolerant_amount_date", conf, matched_refs_set)
                return
            self.transition("TOLERANT_CHECK", res)

            self.transition("FUZZY_CHECK", "started")
            res, b, conf = check_fuzzy_narration(self.txn, cands)
            if res == "matched":
                self._claim_match(b, "fuzzy_narration", conf, matched_refs_set)
                return
            self.transition("FUZZY_CHECK", res)

            self.transition("LLM_CHECK", "started")
            cands_for_llm = cands.copy()
            cands_for_llm["date_diff"] = cands_for_llm["date"].apply(lambda d: abs((d - self.txn["date"]).days))
            cands_for_llm = cands_for_llm[cands_for_llm["date_diff"] <= 3]
            cands_for_llm = cands_for_llm[(cands_for_llm["amount"] >= self.txn["amount"] * 0.8) & (cands_for_llm["amount"] <= self.txn["amount"] * 1.2)]
            
            candidates_list = cands_for_llm.to_dict('records')
            if not candidates_list:
                self.transition("EXCEPTION", "no_plausible_candidate")
                return
                
            # LLM Call is synchronous here, taking thread time (good for IO-bound threading)
            if self.api_key:
                result = call_claude(self.txn, candidates_list, self.api_key)
            else:
                result = mock_llm_call(self.txn, candidates_list)
                
            best_match = result.get("best_match")
            confidence = float(result.get("confidence", 0.0))
            reasoning = result.get("reasoning", "")
            
            if not best_match:
                self.transition("EXCEPTION", "llm_no_confident_match", reasoning)
            elif confidence < 0.75:
                self.transition("EXCEPTION", "llm_low_confidence", reasoning)
            else:
                b_match = cands_for_llm[cands_for_llm["bank_ref"] == best_match].iloc[0]
                self._claim_match(b_match, "llm_resolved", confidence, matched_refs_set)

        except Exception as e:
            self.error = str(e)
            self.transition("pipeline_error", "exception_thrown", str(e))
            
    def _claim_match(self, b_row, match_type, conf, matched_refs_set):
        """Thread-safe claim of a bank candidate."""
        with match_lock:
            if b_row["bank_ref"] in matched_refs_set:
                self.transition("EXCEPTION", "race_condition_lost", "Another agent claimed this bank_ref while processing.")
            else:
                matched_refs_set.add(b_row["bank_ref"])
                self.match = {"bank_ref": b_row["bank_ref"], "match_type": match_type, "confidence": conf}
                self.transition("RESOLVED", match_type, b_row["bank_ref"])


class BankExceptionAgent:
    """Agent for classifying orphaned bank entries (Reverse Reconciliation)"""
    def __init__(self, bank_txn_dict):
        self.txn = bank_txn_dict
        self.id = bank_txn_dict["bank_ref"]
        self.status = "PENDING"
        self.trace = []
        self.classification = None
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")
        
    def transition(self, state, result, details=""):
        self.status = state
        self.trace.append({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "state": state,
            "result": result,
            "details": details
        })
        
    def run(self):
        try:
            self.transition("LLM_CLASSIFY", "started")
            if self.api_key:
                result = classify_bank_entry(self.txn, self.api_key)
            else:
                result = mock_classify_bank_entry(self.txn)
                
            self.classification = result.get("classification", "unknown")
            reasoning = result.get("reasoning", "")
            self.transition("RESOLVED", self.classification, reasoning)
        except Exception as e:
            self.transition("pipeline_error", "exception_thrown", str(e))


def run_orchestrator(gateway_path, bank_path, output_path, workers=4):
    gw, bank = load_data(gateway_path, bank_path)
    
    agents = []
    matched_bank_refs = set()
    matches_for_eval = []
    
    print(f"Orchestrating {len(gw)} gateway transactions using ThreadPool (workers={workers})...")
    
    # Phase 1: Forward Matching (Gateway -> Bank)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = []
        for _, g_row in gw.iterrows():
            agent = TransactionAgent(g_row.to_dict())
            agents.append(agent)
            futures.append(executor.submit(agent.run, bank, matched_bank_refs))
            
        for future in as_completed(futures):
            future.result() # re-raise exceptions if any
            
    # Collect successful matches
    for agent in agents:
        if agent.status == "RESOLVED" and agent.match:
            matches_for_eval.append({
                "transaction_id": agent.id,
                "bank_ref": agent.match["bank_ref"],
                "match_type": agent.match["match_type"],
                "confidence": agent.match["confidence"]
            })
            
    # Phase 2: Reverse Reconciliation (Orphaned Bank -> Ledger)
    orphaned_bank = bank[~bank["bank_ref"].isin(matched_bank_refs)]
    print(f"Phase 2: Classifying {len(orphaned_bank)} orphaned bank exceptions...")
    
    bank_agents = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = []
        for _, b_row in orphaned_bank.iterrows():
            bagent = BankExceptionAgent(b_row.to_dict())
            bank_agents.append(bagent)
            futures.append(executor.submit(bagent.run))
            
        for future in as_completed(futures):
            future.result()
    
    # Build Report
    report = {
        "run_metadata": {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "input_files": {"gateway": gateway_path, "bank": bank_path},
            "total_records": len(gw),
            "bank_records": len(bank),
            "threads": workers
        },
        "summary": {
            "match_rate": round(len(matches_for_eval) / len(gw) * 100, 1) if len(gw) else 0,
        },
        "transactions": [],
        "bank_exceptions": [],
        "gateway_exceptions": []
    }
    
    ground_truth_path = gateway_path.replace("gateway_ledger.csv", "ground_truth.csv")
    try:
        truth = pd.read_csv(ground_truth_path)
        truth_valid = truth.dropna(subset=["transaction_id", "bank_ref"])
        correct_pairs = set(zip(truth_valid["transaction_id"], truth_valid["bank_ref"]))
        
        correct = sum(1 for m in matches_for_eval if (m["transaction_id"], m["bank_ref"]) in correct_pairs)
        report["summary"]["precision"] = round(correct / len(matches_for_eval) * 100, 1) if matches_for_eval else 0
        report["summary"]["by_match_type"] = {}
        
        df_m = pd.DataFrame(matches_for_eval)
        if not df_m.empty:
            for mtype in df_m["match_type"].unique():
                sub = df_m[df_m["match_type"] == mtype]
                c = sum(1 for _, r in sub.iterrows() if (r["transaction_id"], r["bank_ref"]) in correct_pairs)
                report["summary"]["by_match_type"][mtype] = {
                    "count": len(sub),
                    "precision": round(c / len(sub) * 100, 1)
                }
    except FileNotFoundError:
        report["summary"]["precision"] = "ground_truth.csv not found"
            
    for a in agents:
        txn_dict = {k: str(v) if isinstance(v, pd.Timestamp) else v for k, v in a.txn.items()}
        if a.status in ["EXCEPTION", "pipeline_error"]:
            report["gateway_exceptions"].append({
                "transaction_id": a.id,
                "reason": a.trace[-1]["result"] if a.trace else "unknown",
                "details": a.trace[-1].get("details", "") if a.trace else "",
                "trace": a.trace
            })
        else:
            report["transactions"].append({
                "transaction_id": a.id,
                "final_status": a.status,
                "trace": a.trace
            })
            
    for ba in bank_agents:
        report["bank_exceptions"].append({
            "bank_ref": ba.id,
            "classification": ba.classification,
            "reasoning": ba.trace[-1].get("details", "") if ba.trace else "",
            "trace": ba.trace
        })
            
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
        
    print(f"Report written to {output_path}")
    print(f"Match Rate: {report['summary']['match_rate']}%")
    print(f"Precision: {report['summary'].get('precision', 'N/A')}%")
    print(f"Classified {len(bank_agents)} orphaned bank records.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--bank", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()
    run_orchestrator(args.gateway, args.bank, args.output, args.workers)
