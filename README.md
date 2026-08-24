# AI Finance Controller — Multi-Source Reconciliation Agent
Razorpay AI Buildathon 2026 — Track 04

## Developer Setup
To get started with development, set up your virtual environment and install the dependencies. Then, generate the initial synthetic data and run the deterministic matching pipeline:

```bash
# 1. Create and activate virtual environment
python -m venv venv
.\venv\Scripts\activate  # On Windows
# source venv/bin/activate  # On Mac/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Generate the synthetic datasets
cd data
python generate_synthetic_data.py --n 250 --seed 42
cd ..

# 4. Run the deterministic matching engine and evaluation
cd backend
python matcher.py
python evaluate.py
cd ..
```

## Problem
Reconciling a payment gateway ledger against a bank statement is still done
by hand at most companies. Bank narration text is messy, settlement lags by
1-2 days, duplicates happen, and some transactions never make it to either
side. This project builds an agent that closes that loop: match what it can
confidently match, and give an honest, reasoned list of what it can't.

## The bar this is built to clear
- Match rate reported honestly (not just "% matched" but **precision** —
  were the matches actually correct)
- A real exception list with reason codes, not a black box
- A full audit trail: every match has a rule/reason attached
- At least one documented failure mode caught and explained

## Repo structure
```
data/
  generate_synthetic_data.py   # Milestone 2 — synthetic data generator
  gateway_ledger.csv           # generated: payment gateway side
  bank_statement.csv           # generated: messy bank side
  ground_truth.csv             # HIDDEN answer key, dev-time only

backend/
  matcher.py                   # Milestone 3 — deterministic + tolerant matching
  evaluate.py                  # Milestone 3/6 — precision scoring vs ground truth
  nlp/                         # Milestone 4 — LLM-assisted ambiguous-case resolver (next)
  api.py                       # Milestone 6 — FastAPI endpoints (next)

frontend/                      # Milestone 6 — dashboard (next)
docs/
  architecture.png             # Milestone 7
  pitch_script.md              # Milestone 7
```

## How to run what exists so far
```bash
cd data
python3 generate_synthetic_data.py --n 250 --seed 42

cd ../backend
python3 matcher.py
python3 evaluate.py
```

## Results on this run (seed=42, n=250)
- 206 captured gateway transactions
- 145 auto-matched (70.4% match rate)
- 91.0% precision on those matches
- 13 false positives — all traced to deliberately-injected decoy transactions
  (same amount + same/near date, different counterparty) — a known, documented
  weak point of amount+date-only tolerant matching. This is exactly the kind
  of case Milestone 4 (LLM-assisted narration matching) is designed to fix,
  since the decoy's UTR does not actually appear in the true transaction's
  narration.

## What broke / lesson learned (for the "what broke" writeup)
Initial version of the tolerant matcher had no ambiguity check — it would
silently pick the *first* candidate within amount+date tolerance in the
duplicate-bank-entry case, double-counting one gateway transaction against
two bank entries and leaving the real second transaction unmatched. Fixed by
requiring `len(candidates) == 1` before auto-matching, and routing anything
with 2+ candidates to an "ambiguous -> escalate" audit entry instead of
guessing. That change alone is what will let Milestone 4's LLM step add real
value instead of just rubber-stamping bad rule-based guesses.

## 7-Part Milestone Plan
1. Problem scoping & repo skeleton
2. Synthetic data generator — DONE (this commit)
3. Deterministic matching engine — DONE, baseline 91% precision
4. NLP/LLM-assisted resolution for ambiguous cases — NEXT
5. Agent orchestration, exception handling, audit trail — builds on M3/M4
6. Backend API (FastAPI) + UI dashboard
7. Deployment, evaluation write-up, pitch video, architecture diagram
