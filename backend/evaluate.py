
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
    overall_recall = correct / len(correct_pairs) * 100 if correct_pairs else 0

    print(f"Total matches made:     {total}")
    print(f"Correct matches:        {correct} out of {len(correct_pairs)} possible")
    print(f"INCORRECT matches:      {incorrect}")
    print(f"Overall Precision:      {precision:.1f}%")
    print(f"Overall Recall:         {overall_recall:.1f}%\n")

    print("--- Breakdown by Match Type ---")
    print(f"{'Match Type':<30} | {'Count':<6} | {'Precision':<10} | {'Recall':<6}")
    print("-" * 65)
    for mtype in matches["match_type"].unique():
        m_subset = matches[matches["match_type"] == mtype]
        m_correct = sum(1 for _, r in m_subset.iterrows() if (r["transaction_id"], r["bank_ref"]) in correct_pairs)
        m_total = len(m_subset)
        m_prec = m_correct / m_total * 100 if m_total else 0
        m_rec = m_correct / len(correct_pairs) * 100 if correct_pairs else 0
        print(f"{mtype:<30} | {m_total:<6} | {m_prec:>6.1f}%    | {m_rec:>5.1f}%")
    print("-" * 65)

    if wrong_matches:
        print("\n--- WRONG MATCHES (false positives — these matter more than the rate) ---")
        for w in wrong_matches:
            print(w)

    pd.DataFrame(wrong_matches).to_csv("../data/false_positives.csv", index=False)
    return precision, wrong_matches


if __name__ == "__main__":
    evaluate("../data/matches.csv", "../data/ground_truth.csv")
