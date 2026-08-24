"""
Synthetic Data Generator — AI Finance Controller (Track 04)
Razorpay AI Buildathon 2026

Generates two linked-but-messy datasets that simulate a real reconciliation
problem between a payment gateway ledger (Razorpay-style) and a bank statement:

  1. gateway_ledger.csv   -> what the payment gateway recorded
  2. bank_statement.csv   -> what the bank actually shows (messier, delayed, noisy)
  3. ground_truth.csv     -> HIDDEN answer key (transaction_id <-> bank_ref),
                             used only by YOU to score your matching engine.
                             Do NOT feed this to your matcher.

Design goals (mirrors real recon problems on purpose):
  - Exact matches                         (~65%)
  - Settlement lag (T+1 / T+2)             -> date mismatch
  - Paise/rounding differences             -> amount mismatch
  - UTR embedded in noisy bank narration   -> needs NLP extraction
  - Missing on bank side (txn failed after gateway logged it)
  - Missing on gateway side (bank-only entries: charges, refunds, reversals)
  - Duplicate bank entries                 -> tests dedup logic
  - Near-duplicate decoys (same amount, same day, different txn) -> adversarial case

Usage:
    python generate_synthetic_data.py --n 250 --seed 42
"""

import argparse
import csv
import random
import string
from datetime import datetime, timedelta

try:
    from faker import Faker
    fake = Faker("en_IN")
except ImportError:
    fake = None

MERCHANT_NARRATION_TEMPLATES = [
    "UPI/{utr}/PAYMENT FROM {name}",
    "NEFT-{utr}-{name}-RAZORPAY",
    "IMPS/{utr}/{name}/RZP",
    "RTGS {utr} {name} SETTLEMENT",
    "UPI-{utr_lower}-{name}-OK",
    "TO TRANSFER-UPI/{utr}/{name}",
    "*{utr}* {name} NEFT CR",
    "RZRPY/{utr}/{name}",
]

BANK_ONLY_NARRATIONS = [
    "BANK CHARGES - AMC",
    "GST ON BANK CHARGES",
    "INTEREST CREDIT SAVINGS AC",
    "CASH DEPOSIT SELF",
    "REVERSAL - DUPLICATE DEBIT",
    "CHEQUE RETURN CHARGES",
]

STATUSES = ["captured", "captured", "captured", "captured", "failed"]


def rand_utr(rng):
    return "".join(rng.choices(string.ascii_uppercase + string.digits, k=12))


def rand_name(rng):
    if fake:
        return fake.name().upper().replace(" ", "")
    names = ["RAHULSHARMA", "PRIYAVERMA", "AMITPATEL", "SNEHASINGH", "VIKRAMRAO"]
    return rng.choice(names)


def messy_amount(amount, rng):
    """Introduce paise-level rounding noise some % of the time."""
    r = rng.random()
    if r < 0.08:
        return round(amount + rng.choice([-1, 1]) * rng.uniform(0.5, 3.0), 2)  # rounding diff
    return round(amount, 2)


def build_narration(utr, name, rng):
    template = rng.choice(MERCHANT_NARRATION_TEMPLATES)
    return template.format(utr=utr, utr_lower=utr.lower(), name=name)


def generate(n, seed, outdir, report=False):
    rng = random.Random(seed)
    start_date = datetime(2026, 6, 1)

    gateway_rows = []
    bank_rows = []
    ground_truth = []

    bank_ref_counter = 90000

    for i in range(1, n + 1):
        txn_id = f"pay_{rng.randint(10**9, 10**10 - 1)}"
        utr = rand_utr(rng)
        name = rand_name(rng)
        amount = round(rng.uniform(150, 45000), 2)
        gw_timestamp = start_date + timedelta(
            days=rng.randint(0, 29), hours=rng.randint(0, 23), minutes=rng.randint(0, 59)
        )
        status = rng.choices(STATUSES, weights=[70, 5, 5, 5, 15])[0] if False else rng.choice(STATUSES)

        gateway_rows.append({
            "transaction_id": txn_id,
            "amount": round(amount, 2),
            "timestamp": gw_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "utr": utr,
        })

        # Decide this record's "fate" on the bank side
        fate = rng.random()

        if status == "failed":
            # Failed gateway txns should NOT appear on the bank side at all
            continue

        if fate < 0.65:
            # Clean, straightforward match (possibly with small settlement lag)
            lag_days = rng.choice([0, 0, 0, 1, 2])
            value_date = gw_timestamp + timedelta(days=lag_days)
            bank_amount = messy_amount(amount, rng)
            bank_ref_counter += 1
            bref = f"BNK{bank_ref_counter}"
            bank_rows.append({
                "bank_ref": bref,
                "amount": bank_amount,
                "value_date": value_date.strftime("%Y-%m-%d"),
                "narration": build_narration(utr, name, rng),
            })
            ground_truth.append({"transaction_id": txn_id, "bank_ref": bref, "match_type": "exact_or_tolerant"})

        elif fate < 0.78:
            # Duplicate bank entry (bank double-posted) -> should be flagged, not double-counted
            lag_days = rng.choice([0, 1])
            value_date = gw_timestamp + timedelta(days=lag_days)
            bank_ref_counter += 1
            bref1 = f"BNK{bank_ref_counter}"
            bank_ref_counter += 1
            bref2 = f"BNK{bank_ref_counter}"
            narration = build_narration(utr, name, rng)
            bank_rows.append({"bank_ref": bref1, "amount": amount, "value_date": value_date.strftime("%Y-%m-%d"), "narration": narration})
            bank_rows.append({"bank_ref": bref2, "amount": amount, "value_date": value_date.strftime("%Y-%m-%d"), "narration": narration + " DUP"})
            ground_truth.append({"transaction_id": txn_id, "bank_ref": bref1, "match_type": "duplicate_pair"})
            ground_truth.append({"transaction_id": txn_id, "bank_ref": bref2, "match_type": "duplicate_pair"})

        elif fate < 0.92:
            # Missing on bank side entirely (e.g. gateway shows captured, settlement pending/lost)
            ground_truth.append({"transaction_id": txn_id, "bank_ref": None, "match_type": "missing_on_bank"})

        elif fate < 0.97:
            # Partial refund: gateway transaction matches a bank entry for a smaller amount
            lag_days = rng.choice([0, 1, 2])
            value_date = gw_timestamp + timedelta(days=lag_days)
            refund_amount = round(rng.uniform(10, amount * 0.5), 2)
            bank_amount = round(amount - refund_amount, 2)
            bank_ref_counter += 1
            bref = f"BNK{bank_ref_counter}"
            bank_rows.append({
                "bank_ref": bref,
                "amount": bank_amount,
                "value_date": value_date.strftime("%Y-%m-%d"),
                "narration": build_narration(utr, name, rng),
            })
            ground_truth.append({"transaction_id": txn_id, "bank_ref": bref, "match_type": "partial_refund"})

        else:
            # Near-duplicate decoy: same amount, same day, DIFFERENT unrelated transaction
            # This creates a genuinely ambiguous case for the matcher to test against.
            lag_days = rng.choice([0, 1])
            value_date = gw_timestamp + timedelta(days=lag_days)
            decoy_utr = rand_utr(rng)
            decoy_name = rand_name(rng)
            bank_ref_counter += 1
            bref = f"BNK{bank_ref_counter}"
            bank_rows.append({
                "bank_ref": bref,
                "amount": amount,  # same amount on purpose -> adversarial trap
                "value_date": value_date.strftime("%Y-%m-%d"),
                "narration": build_narration(decoy_utr, decoy_name, rng),  # unrelated UTR
            })
            ground_truth.append({"transaction_id": txn_id, "bank_ref": None, "match_type": "missing_on_bank_with_decoy"})
            ground_truth.append({"transaction_id": f"DECOY_FOR_{txn_id}", "bank_ref": bref, "match_type": "decoy_no_gateway_counterpart"})

    # Bank-only noise: charges, interest, reversals with no gateway counterpart at all
    n_bank_only = max(3, n // 20)
    for _ in range(n_bank_only):
        bank_ref_counter += 1
        bref = f"BNK{bank_ref_counter}"
        d = start_date + timedelta(days=rng.randint(0, 29))
        bank_rows.append({
            "bank_ref": bref,
            "amount": round(rng.uniform(20, 5000), 2),
            "value_date": d.strftime("%Y-%m-%d"),
            "narration": rng.choice(BANK_ONLY_NARRATIONS),
        })
        ground_truth.append({"transaction_id": None, "bank_ref": bref, "match_type": "bank_only_no_gateway"})

    rng.shuffle(bank_rows)

    # --- write files ---
    with open(f"{outdir}/gateway_ledger.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["transaction_id", "amount", "timestamp", "status", "utr"])
        w.writeheader()
        w.writerows(gateway_rows)

    with open(f"{outdir}/bank_statement.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["bank_ref", "amount", "value_date", "narration"])
        w.writeheader()
        w.writerows(bank_rows)

    with open(f"{outdir}/ground_truth.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["transaction_id", "bank_ref", "match_type"])
        w.writeheader()
        w.writerows(ground_truth)

    print(f"Generated {len(gateway_rows)} gateway records, {len(bank_rows)} bank records")
    print(f"Ground truth entries: {len(ground_truth)}")
    print(f"Files written to: {outdir}/")

    if report:
        from collections import Counter
        counts = Counter(row["match_type"] for row in ground_truth)
        total = len(ground_truth)
        print("\n--- Match Type Distribution ---")
        for mtype, count in counts.most_common():
            print(f"{mtype:<30} {count:>5} ({count/total*100:.1f}%)")
        print("-------------------------------")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=250, help="number of base transactions")
    parser.add_argument("--scale", type=int, default=1, help="multiplier for n to scale up dataset")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--outdir", type=str, default=".")
    parser.add_argument("--report", action="store_true", help="print distribution report")
    args = parser.parse_args()
    
    total_n = args.n * args.scale
    generate(total_n, args.seed, args.outdir, args.report)
