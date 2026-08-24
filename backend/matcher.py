"""
Milestone 3 + 4 baseline: Deterministic + NLP-assisted reconciliation matcher.
Reads gateway_ledger.csv + bank_statement.csv, produces:
  - matches.csv       (matched pairs with match_type + confidence)
  - exceptions.csv     (unresolved records with reason codes)
  - audit_log.csv       (every decision the engine made, and why)

This is a BASELINE — Milestone 4 replaces the "fuzzy_llm" stub with a real
LLM call (see nlp/llm_matcher.py placeholder at the bottom).
"""

import re
import csv
import pandas as pd
from rapidfuzz import fuzz

UTR_REGEX = re.compile(r"[A-Z0-9]{10,16}")


def extract_utr_candidates(narration: str):
    """Pull anything that *looks* like a UTR/reference token out of noisy bank text."""
    return UTR_REGEX.findall(narration.upper())


def load_data(gateway_path, bank_path):
    gw = pd.read_csv(gateway_path)
    bank = pd.read_csv(bank_path)
    gw = gw[gw["status"] == "captured"].copy()  # failed txns never hit the bank
    bank["value_date"] = pd.to_datetime(bank["value_date"])
    gw["timestamp"] = pd.to_datetime(gw["timestamp"])
    gw["date"] = gw["timestamp"].dt.date
    bank["date"] = bank["value_date"].dt.date
    return gw, bank


def run_matching(gateway_path, bank_path, outdir="."):
    gw, bank = load_data(gateway_path, bank_path)

    matches = []
    audit = []
    matched_bank_refs = set()

    # ---- PASS 1: exact UTR match embedded in narration ----
    for _, g in gw.iterrows():
        candidates = bank[~bank["bank_ref"].isin(matched_bank_refs)]
        exact_hit = candidates[candidates["narration"].str.upper().str.contains(g["utr"], na=False)]
        if len(exact_hit) == 1:
            b = exact_hit.iloc[0]
            matches.append({
                "transaction_id": g["transaction_id"], "bank_ref": b["bank_ref"],
                "match_type": "exact_utr", "confidence": 1.0,
                "amount_diff": round(abs(g["amount"] - b["amount"]), 2),
                "date_diff_days": abs((g["date"] - b["date"]).days),
            })
            matched_bank_refs.add(b["bank_ref"])
            audit.append({"transaction_id": g["transaction_id"], "rule": "exact_utr_in_narration",
                           "result": "matched", "bank_ref": b["bank_ref"]})
        elif len(exact_hit) > 1:
            audit.append({"transaction_id": g["transaction_id"], "rule": "exact_utr_in_narration",
                           "result": "ambiguous_multiple_hits", "bank_ref": None})

    # ---- PASS 2: tolerant amount+date match for unresolved gateway txns ----
    unresolved_gw = gw[~gw["transaction_id"].isin([m["transaction_id"] for m in matches])]
    for _, g in unresolved_gw.iterrows():
        candidates = bank[~bank["bank_ref"].isin(matched_bank_refs)].copy()
        candidates["amount_diff"] = (candidates["amount"] - g["amount"]).abs()
        candidates["date_diff"] = candidates["date"].apply(lambda d: abs((d - g["date"]).days))
        tight = candidates[(candidates["amount_diff"] <= 5.0) & (candidates["date_diff"] <= 2)]

        if len(tight) == 1:
            b = tight.iloc[0]
            conf = 0.85 if b["amount_diff"] < 0.01 else 0.7
            matches.append({
                "transaction_id": g["transaction_id"], "bank_ref": b["bank_ref"],
                "match_type": "tolerant_amount_date", "confidence": conf,
                "amount_diff": round(b["amount_diff"], 2), "date_diff_days": int(b["date_diff"]),
            })
            matched_bank_refs.add(b["bank_ref"])
            audit.append({"transaction_id": g["transaction_id"], "rule": "tolerant_amount_date",
                           "result": "matched", "bank_ref": b["bank_ref"]})
        elif len(tight) > 1:
            # genuinely ambiguous -> this is exactly what should go to the LLM step (Milestone 4)
            audit.append({"transaction_id": g["transaction_id"], "rule": "tolerant_amount_date",
                           "result": f"ambiguous_{len(tight)}_candidates -> escalate_to_llm", "bank_ref": None})
        else:
            audit.append({"transaction_id": g["transaction_id"], "rule": "tolerant_amount_date",
                           "result": "no_candidate_found", "bank_ref": None})

    # ---- Build exceptions list ----
    matched_txn_ids = {m["transaction_id"] for m in matches}
    exceptions = []
    for _, g in gw.iterrows():
        if g["transaction_id"] not in matched_txn_ids:
            exceptions.append({"record_type": "gateway_txn", "id": g["transaction_id"],
                                "amount": g["amount"], "reason": "no_confident_bank_match_found"})
    for _, b in bank.iterrows():
        if b["bank_ref"] not in matched_bank_refs:
            exceptions.append({"record_type": "bank_entry", "id": b["bank_ref"],
                                "amount": b["amount"], "reason": "no_gateway_counterpart_or_ambiguous"})

    # ---- write outputs ----
    pd.DataFrame(matches).to_csv(f"{outdir}/matches.csv", index=False)
    pd.DataFrame(exceptions).to_csv(f"{outdir}/exceptions.csv", index=False)
    pd.DataFrame(audit).to_csv(f"{outdir}/audit_log.csv", index=False)

    match_rate = len(matches) / len(gw) * 100 if len(gw) else 0
    print(f"Gateway captured txns: {len(gw)}")
    print(f"Matched: {len(matches)}  ({match_rate:.1f}% match rate)")
    print(f"Exceptions: {len(exceptions)}")
    return matches, exceptions, audit


if __name__ == "__main__":
    run_matching(
        gateway_path="../data/gateway_ledger.csv",
        bank_path="../data/bank_statement.csv",
        outdir="../data",
    )
