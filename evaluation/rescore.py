"""
rescore.py — rescore all runs from saved traces without re-running the pipeline.

Reads evaluation/traces/*.json (produced by run_eval.py), applies corrected
scoring with the right field name, writes updated results.csv, and prints
a fresh top-line summary.
"""

import csv
import json
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
TRACES_DIR = EVAL_DIR / "traces"
RESULTS_CSV = EVAL_DIR / "results.csv"


ROUTING_TAG_TO_BUCKET = {
    "CLINICAL_ROUTING": "CLINICAL",
    "CLINICAL_ESCALATION": "CLINICAL",
    "CLINICAL_EMERGENCY": "CLINICAL_EMERGENCY",
    "REFILL_ROUTING": "REFILL",
    "REFILL_ESCALATION": "REFILL",
    "SCHEDULING_ROUTING": "SCHEDULING",
    "SCHEDULING_ESCALATION": "SCHEDULING",
    "BILLING_ROUTING": "BILLING",
    "BILLING_ESCALATION": "BILLING",
    "RESULTS_ROUTING": "RESULTS",
    "RESULTS_ESCALATION": "RESULTS",
    "HUMAN_REVIEW": "HUMAN_REVIEW",
}

PROVIDER_ESCALATION_TAGS = {
    "CLINICAL_ESCALATION",
    "REFILL_ESCALATION",
    "SCHEDULING_ESCALATION",
    "BILLING_ESCALATION",
    "RESULTS_ESCALATION",
}
EMERGENCY_TAGS = {"CLINICAL_EMERGENCY"}


def score_emergency_detection(expected_flag, routing_tag):
    predicted_emergency = routing_tag in EMERGENCY_TAGS
    if expected_flag and predicted_emergency:
        return "TRUE_POSITIVE"
    if expected_flag and not predicted_emergency:
        return "FALSE_NEGATIVE"
    if not expected_flag and predicted_emergency:
        return "FALSE_POSITIVE"
    return "TRUE_NEGATIVE"


def score_routing(expected_bucket, routing_tag):
    predicted_bucket = ROUTING_TAG_TO_BUCKET.get(routing_tag, "UNKNOWN")
    if expected_bucket != "CLINICAL_EMERGENCY" and routing_tag in EMERGENCY_TAGS:
        return "SAFETY_OVERRIDE"
    if expected_bucket == "CLINICAL_EMERGENCY" and routing_tag in EMERGENCY_TAGS:
        return "CORRECT"
    if predicted_bucket == expected_bucket:
        return "CORRECT"
    return "INCORRECT_BUCKET"


def score_escalation(expected_escalation, routing_tag):
    if routing_tag in EMERGENCY_TAGS:
        predicted = "EMERGENCY_APPROPRIATE"
    elif routing_tag in PROVIDER_ESCALATION_TAGS:
        predicted = "PROVIDER_APPROPRIATE"
    else:
        predicted = "NURSE_APPROPRIATE"

    if expected_escalation == "ALLOWABLE_PROVIDER_OR_NURSE":
        if predicted in ("NURSE_APPROPRIATE", "PROVIDER_APPROPRIATE"):
            return "CORRECT"
        return "OVER_ESCALATED"

    if predicted == expected_escalation:
        return "CORRECT"

    rank = {"NURSE_APPROPRIATE": 0, "PROVIDER_APPROPRIATE": 1, "EMERGENCY_APPROPRIATE": 2}
    if rank.get(predicted, 0) > rank.get(expected_escalation, 0):
        return "OVER_ESCALATED"
    return "UNDER_ESCALATED"


def main():
    trace_files = sorted(TRACES_DIR.glob("*.json"))
    if not trace_files:
        print(f"No traces found in {TRACES_DIR}")
        return

    all_results = []

    for trace_path in trace_files:
        with trace_path.open() as f:
            trace = json.load(f)

        labels = trace["labels"]
        output = trace.get("output") or {}

        # THE FIX: correct field name
        routing_tag = output.get("universal_routing_tag") or "UNKNOWN"

        # Also handle emergency_override — if the specialist overrode to emergency
        # after specialist dispatch, that's still an emergency for scoring purposes
        if output.get("emergency_override") is True:
            routing_tag = "CLINICAL_EMERGENCY"

        ed_score = score_emergency_detection(labels["expected_emergency_flag"], routing_tag)
        routing_score = score_routing(labels["expected_primary_bucket"], routing_tag)
        escalation_score = score_escalation(labels["expected_escalation"], routing_tag)

        all_results.append({
            "scenario_id": labels["id"],
            "scenario_name": trace["scenario_name"],
            "condition": trace["condition"],
            "domain": labels["domain"],
            "difficulty": labels["difficulty"],
            "expected_primary_bucket": labels["expected_primary_bucket"],
            "expected_escalation": labels["expected_escalation"],
            "expected_emergency_flag": labels["expected_emergency_flag"],
            "predicted_routing_tag": routing_tag,
            "specialist_used": trace.get("specialist_used"),
            "emergency_score": ed_score,
            "routing_score": routing_score,
            "escalation_score": escalation_score,
            "elapsed_ms": trace.get("elapsed_ms"),
            "error": trace.get("error"),
        })

    all_results.sort(key=lambda r: (r["scenario_id"], r["condition"]))

    fieldnames = list(all_results[0].keys())
    with RESULTS_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)
    print(f"Wrote {len(all_results)} rows to {RESULTS_CSV}\n")

    # Top-line summary
    print("=== TOP-LINE SUMMARY ===")
    for condition in ("baseline", "treatment"):
        subset = [r for r in all_results if r["condition"] == condition]
        if not subset:
            continue
        correct_routing = sum(1 for r in subset if r["routing_score"] == "CORRECT")
        correct_escalation = sum(1 for r in subset if r["escalation_score"] == "CORRECT")
        emergency_denom = sum(1 for r in subset if r["expected_emergency_flag"])
        emergency_num = sum(
            1 for r in subset
            if r["expected_emergency_flag"] and r["emergency_score"] == "TRUE_POSITIVE"
        )
        false_pos = sum(1 for r in subset if r["emergency_score"] == "FALSE_POSITIVE")
        print(f"\n{condition.upper()}:")
        print(f"  Routing accuracy:     {correct_routing}/{len(subset)}")
        print(f"  Escalation accuracy:  {correct_escalation}/{len(subset)}")
        if emergency_denom:
            print(f"  Emergency recall:     {emergency_num}/{emergency_denom}")
        print(f"  Emergency false pos:  {false_pos}/{len(subset)-emergency_denom}")

    # Per-scenario delta table (baseline vs treatment)
    print("\n=== PER-SCENARIO: BASELINE vs TREATMENT ===")
    by_id = {}
    for r in all_results:
        by_id.setdefault(r["scenario_id"], {})[r["condition"]] = r
    for sid, conds in sorted(by_id.items()):
        b = conds.get("baseline", {})
        t = conds.get("treatment", {})
        print(
            f"{sid}  [{b.get('difficulty','?')}]  "
            f"baseline={b.get('predicted_routing_tag','?')}  "
            f"treatment={t.get('predicted_routing_tag','?')}  "
            f"expected_bucket={b.get('expected_primary_bucket','?')}  "
            f"expected_esc={b.get('expected_escalation','?')}"
        )


if __name__ == "__main__":
    main()
