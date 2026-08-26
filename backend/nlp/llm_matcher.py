import os
import csv
import json
import pandas as pd
from typing import List, Dict
import sys

try:
    import anthropic
except ImportError:
    anthropic = None

def mock_llm_call(gateway_txn: dict, candidates: List[dict]) -> dict:
    """
    Mock LLM function for local testing without an API key.
    Rejects decoy transactions by verifying UTR context in narration.
    """
    g_utr = str(gateway_txn["utr"]).upper()
    for c in candidates:
        if g_utr in str(c["narration"]).upper():
            return {
                "best_match": c["bank_ref"],
                "confidence": 0.95,
                "reasoning": "UTR found in narration context, highly likely match."
            }
    
    return {
        "best_match": None,
        "confidence": 0.99,
        "reasoning": "No candidates share UTR or merchant context. Likely decoys."
    }

def call_claude(gateway_txn: dict, candidates: List[dict], api_key: str) -> dict:
    if not anthropic:
        raise ImportError("anthropic package not installed")
        
    client = anthropic.Anthropic(api_key=api_key)
    
    prompt = f"""You are an expert financial reconciliation analyst.
Your job is to match a gateway transaction to the best bank statement candidate, if one exists.
Gateway Transaction:
- ID: {gateway_txn['transaction_id']}
- Amount: {gateway_txn['amount']}
- Date: {gateway_txn['timestamp']}
- UTR: {gateway_txn['utr']}

Bank Candidates:
"""
    for c in candidates:
        prompt += f"- Bank Ref: {c['bank_ref']}, Amount: {c['amount']}, Date: {c['value_date']}, Narration: {c['narration']}\n"
        
    prompt += """
Respond STRICTLY in JSON format without markdown formatting, using this schema:
{"best_match": "<bank_ref or null>", "confidence": <float 0-1>, "reasoning": "<one sentence, max 25 words>"}
"""

    response = client.messages.create(
        model="claude-3-5-sonnet-20240620",
        max_tokens=200,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}]
    )
    
    # Parse defensively
    content = response.content[0].text.strip()
    if content.startswith("```json"):
        content = content.replace("```json", "", 1)
    if content.endswith("```"):
        content = content[:-3]
    return json.loads(content.strip())

def mock_classify_bank_entry(bank_txn: dict) -> dict:
    """Mock for classifying orphaned bank transactions."""
    narration = str(bank_txn.get("narration", "")).upper()
    if "FEE" in narration or "CHG" in narration:
        classification = "bank_fee"
    elif "INT" in narration or "CREDIT" in narration:
        classification = "interest_credit"
    else:
        classification = "unmatched_deposit"
        
    return {
        "classification": classification,
        "confidence": 0.90,
        "reasoning": f"Mock classification based on narration keywords."
    }

def classify_bank_entry(bank_txn: dict, api_key: str) -> dict:
    if not anthropic:
        raise ImportError("anthropic package not installed")
        
    client = anthropic.Anthropic(api_key=api_key)
    
    prompt = f"""You are an expert financial reconciliation analyst.
Your job is to classify an orphaned bank transaction that has no corresponding gateway record.
Bank Transaction:
- Bank Ref: {bank_txn['bank_ref']}
- Amount: {bank_txn['amount']}
- Date: {bank_txn['value_date']}
- Narration: {bank_txn['narration']}

Categorize it into one of these strict types: "bank_fee", "interest_credit", "unmatched_deposit", "refund_reversal", or "unknown".

Respond STRICTLY in JSON format without markdown formatting, using this schema:
{{"classification": "<type>", "confidence": <float 0-1>, "reasoning": "<one sentence>"}}
"""

    response = client.messages.create(
        model="claude-3-5-sonnet-20240620",
        max_tokens=150,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}]
    )
    
    content = response.content[0].text.strip()
    if content.startswith("```json"):
        content = content.replace("```json", "", 1)
    if content.endswith("```"):
        content = content[:-3]
    return json.loads(content.strip())
