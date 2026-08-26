import re
import csv
import pandas as pd
from rapidfuzz import fuzz

UTR_REGEX = re.compile(r"[A-Z0-9]{10,16}")


def extract_utr_candidates(narration: str):
    return UTR_REGEX.findall(narration.upper())


def load_data(gateway_path, bank_path):
    gw = pd.read_csv(gateway_path, encoding_errors="replace")
    bank = pd.read_csv(bank_path, encoding_errors="replace")
    gw = gw[gw["status"] == "captured"].copy()
    bank["value_date"] = pd.to_datetime(bank["value_date"])
    gw["timestamp"] = pd.to_datetime(gw["timestamp"])
    gw["date"] = gw["timestamp"].dt.date
    bank["date"] = bank["value_date"].dt.date
    return gw, bank


def check_exact_utr(g, candidates):
    exact_hit = candidates[candidates["narration"].str.upper().str.contains(str(g["utr"]), na=False)]
    if len(exact_hit) == 1:
        return "matched", exact_hit.iloc[0], 1.0
    elif len(exact_hit) > 1:
        return "ambiguous_multiple_hits", None, 0.0
    return "no_candidate_found", None, 0.0


def check_tolerant_amount_date(g, candidates):
    cands = candidates.copy()
    cands["amount_diff"] = (cands["amount"] - g["amount"]).abs()
    cands["date_diff"] = cands["date"].apply(lambda d: abs((d - g["date"]).days))
    tight = cands[(cands["amount_diff"] <= 5.0) & (cands["date_diff"] <= 2)]

    if len(tight) == 1:
        b = tight.iloc[0]
        conf = 0.85 if b["amount_diff"] < 0.01 else 0.7
        return "matched", b, conf
    elif len(tight) > 1:
        return f"ambiguous_{len(tight)}_candidates -> escalate_to_llm", None, 0.0
    return "no_candidate_found", None, 0.0


def check_fuzzy_narration(g, candidates):
    cands = candidates.copy()
    if cands.empty:
        return "no_candidate_found", None, 0.0
    query = str(g["utr"])
    cands["fuzzy_score"] = cands["narration"].apply(lambda x: fuzz.token_set_ratio(query, str(x)))
    cands = cands.sort_values(by="fuzzy_score", ascending=False)
    
    if cands.iloc[0]["fuzzy_score"] > 85:
        is_ambiguous = False
        if len(cands) > 1:
            if (cands.iloc[0]["fuzzy_score"] - cands.iloc[1]["fuzzy_score"]) < 15:
                is_ambiguous = True
                
        if not is_ambiguous:
            return "matched", cands.iloc[0], 0.8
        else:
            return "ambiguous_fuzzy_scores", None, 0.0
    return "no_high_score", None, 0.0


def check_partial_refund(g, candidates):
    cands = candidates.copy()
    if cands.empty:
        return "no_candidate_found", None, 0.0
    cands["date_diff"] = cands["date"].apply(lambda d: abs((d - g["date"]).days))
    cands = cands[cands["date_diff"] <= 2]
    cands = cands[(cands["amount"] >= g["amount"] * 0.5) & (cands["amount"] <= g["amount"] * 0.99)]
    
    if len(cands) == 1:
        return "review_candidate", cands.iloc[0], 0.6
    elif len(cands) > 1:
        return "ambiguous_multiple_candidates", None, 0.0
    return "no_candidate_found", None, 0.0


def run_matching(gateway_path, bank_path, outdir="."):
    gw, bank = load_data(gateway_path, bank_path)

    matches = []
    audit = []
    matched_bank_refs = set()

    for _, g in gw.iterrows():
        # PASS 1
        cands = bank[~bank["bank_ref"].isin(matched_bank_refs)]
        res, b, conf = check_exact_utr(g, cands)
        if res == "matched":
            matches.append({
                "transaction_id": g["transaction_id"], "bank_ref": b["bank_ref"],
                "match_type": "exact_utr", "confidence": conf,
                "amount_diff": round(abs(g["amount"] - b["amount"]), 2),
                "date_diff_days": abs((g["date"] - b["date"]).days),
            })
            matched_bank_refs.add(b["bank_ref"])
            audit.append({"transaction_id": g["transaction_id"], "rule": "exact_utr_in_narration",
                           "result": res, "bank_ref": b["bank_ref"]})
            continue
        elif res != "no_candidate_found":
            audit.append({"transaction_id": g["transaction_id"], "rule": "exact_utr_in_narration",
                           "result": res, "bank_ref": None})
            
    # PASS 2
    unresolved_gw = gw[~gw["transaction_id"].isin([m["transaction_id"] for m in matches])]
    for _, g in unresolved_gw.iterrows():
        cands = bank[~bank["bank_ref"].isin(matched_bank_refs)]
        res, b, conf = check_tolerant_amount_date(g, cands)
        if res == "matched":
            matches.append({
                "transaction_id": g["transaction_id"], "bank_ref": b["bank_ref"],
                "match_type": "tolerant_amount_date", "confidence": conf,
                "amount_diff": round(abs(g["amount"] - b["amount"]), 2), "date_diff_days": abs((g["date"] - b["date"]).days),
            })
            matched_bank_refs.add(b["bank_ref"])
            audit.append({"transaction_id": g["transaction_id"], "rule": "tolerant_amount_date",
                           "result": res, "bank_ref": b["bank_ref"]})
        else:
            audit.append({"transaction_id": g["transaction_id"], "rule": "tolerant_amount_date",
                           "result": res, "bank_ref": None})

    # PASS 3
    unresolved_gw = gw[~gw["transaction_id"].isin([m["transaction_id"] for m in matches])]
    for _, g in unresolved_gw.iterrows():
        cands = bank[~bank["bank_ref"].isin(matched_bank_refs)]
        res, b, conf = check_fuzzy_narration(g, cands)
        if res == "matched":
            matches.append({
                "transaction_id": g["transaction_id"], "bank_ref": b["bank_ref"],
                "match_type": "fuzzy_narration", "confidence": conf,
                "amount_diff": round(abs(g["amount"] - b["amount"]), 2),
                "date_diff_days": abs((g["date"] - b["date"]).days),
            })
            matched_bank_refs.add(b["bank_ref"])
            audit.append({"transaction_id": g["transaction_id"], "rule": "fuzzy_narration",
                           "result": res, "bank_ref": b["bank_ref"]})
        else:
            audit.append({"transaction_id": g["transaction_id"], "rule": "fuzzy_narration",
                           "result": res, "bank_ref": None})

    # PASS 4
    unresolved_gw = gw[~gw["transaction_id"].isin([m["transaction_id"] for m in matches])]
    for _, g in unresolved_gw.iterrows():
        cands = bank[~bank["bank_ref"].isin(matched_bank_refs)]
        res, b, conf = check_partial_refund(g, cands)
        if res == "review_candidate":
            matches.append({
                "transaction_id": g["transaction_id"], "bank_ref": b["bank_ref"],
                "match_type": "partial_refund_candidate", "confidence": conf,
                "amount_diff": round(abs(g["amount"] - b["amount"]), 2),
                "date_diff_days": abs((g["date"] - b["date"]).days),
            })
            matched_bank_refs.add(b["bank_ref"])
            audit.append({"transaction_id": g["transaction_id"], "rule": "partial_refund_detection",
                           "result": res, "bank_ref": b["bank_ref"]})
        elif res != "no_candidate_found":
            audit.append({"transaction_id": g["transaction_id"], "rule": "partial_refund_detection",
                           "result": res, "bank_ref": None})

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
