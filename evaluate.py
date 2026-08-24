"""
Milestone 3/6: Honest evaluation against ground truth.
This is what the buildathon bar means by "measured accuracy" — not just a
match-rate percentage, but PRECISION (were the matches actually correct?).

Run this yourself during development. In your real submission, ground_truth.csv
represents information you would NOT have in production — it's your dev-time
scoring tool, not part of the pipeline.
"""

import pandas as pd


def evaluate(matches_path, ground_truth_path):
    matches = pd.read_csv(matches_path)
    truth = pd.read_csv(ground_truth_path)

    # Build a lookup of correct (transaction_id -> set of valid bank_refs)
    truth_valid = truth.dropna(subset=["transaction_id", "bank_ref"])
    correct_pairs = set(zip(truth_valid["transaction_id"], truth_valid["bank_ref"]))

    correct = 0
    incorrect = 0
    wrong_matches = []

    for _, row in matches.iterrows():
        pair = (row["transaction_id"], row["bank_ref"])
        if pair in correct_pairs:
            correct += 1
        else:
            incorrect += 1
            wrong_matches.append(dict(row))

    total = len(matches)
    precision = correct / total * 100 if total else 0

    print(f"Total matches made:     {total}")
    print(f"Correct matches:        {correct}")
    print(f"INCORRECT matches:      {incorrect}")
    print(f"Precision:              {precision:.1f}%")

    if wrong_matches:
        print("\n--- WRONG MATCHES (false positives — these matter more than the rate) ---")
        for w in wrong_matches:
            print(w)

    pd.DataFrame(wrong_matches).to_csv("false_positives.csv", index=False)
    return precision, wrong_matches


if __name__ == "__main__":
    evaluate("matches.csv", "../data/ground_truth.csv")
