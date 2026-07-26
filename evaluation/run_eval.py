"""
run_eval.py — Baseline vs. Treatment evaluation of Intercept.

Design:
- Baseline: message only (abridge_note stripped to None)
- Treatment: message + abridge_note (as designed)
- All other pipeline components identical

Outputs:
- evaluation/results.csv     — per-scenario, per-condition results
- evaluation/traces/*.json   — full per-run trace for later analysis
"""

import asyncio
import csv
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import InterceptEngine  # noqa: E402
from mock_data import CLINICAL_SCENARIOS  # noqa: E402
from evaluation.scenario_labels import SCENARIO_LABELS  # noqa: E402


EVAL_DIR = Path(__file__).resolve().parent
TRACES_DIR = EVAL_DIR / "traces"
RESULTS_CSV = EVAL_DIR / "results.csv"
TRACES_DIR.mkdir(exist_ok=True)


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


async def run_scenario(engine, scenario_name, scenario, labels, condition):
    message_text = scenario["raw_message"]
    abridge_note = None if condition == "baseline" else scenario["abridge_context"]

    t0 = time.monotonic()
    try:
        specialist_used, output, diagnostics = await engine.process_message(
            message_text, abridge_note=abridge_note
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        error = None
        output_dict = output.model_dump() if hasattr(output, "model_dump") else dict(output)
        routing_tag = (
            output_dict.get("routing_tag")
            or output_dict.get("route")
            or output_dict.get("destination")
            or "UNKNOWN"
        )
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        specialist_used = None
        output_dict = {}
        diagnostics = {}
        routing_tag = "ERROR"
        error = f"{type(e).__name__}: {e}"

    ed_score = score_emergency_detection(labels["expected_emergency_flag"], routing_tag)
    routing_score = score_routing(labels["expected_primary_bucket"], routing_tag)
    escalation_score = score_escalation(labels["expected_escalation"], routing_tag)

    result = {
        "scenario_id": labels["id"],
        "scenario_name": scenario_name,
        "condition": condition,
        "domain": labels["domain"],
        "difficulty": labels["difficulty"],
        "expected_primary_bucket": labels["expected_primary_bucket"],
        "expected_escalation": labels["expected_escalation"],
        "expected_emergency_flag": labels["expected_emergency_flag"],
        "predicted_routing_tag": routing_tag,
        "specialist_used": specialist_used,
        "emergency_score": ed_score,
        "routing_score": routing_score,
        "escalation_score": escalation_score,
        "elapsed_ms": round(elapsed_ms, 0),
        "error": error,
    }

    trace_path = TRACES_DIR / f"{labels['id']}_{condition}.json"
    with trace_path.open("w") as f:
        json.dump({
            "scenario_id": labels["id"],
            "scenario_name": scenario_name,
            "condition": condition,
            "labels": labels,
            "message": message_text,
            "abridge_note_used": abridge_note is not None,
            "specialist_used": specialist_used,
            "output": output_dict,
            "diagnostics": diagnostics,
            "elapsed_ms": round(elapsed_ms, 0),
            "error": error,
        }, f, indent=2, default=str)

    return result


async def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    engine = InterceptEngine(api_key=api_key)

    all_results = []
    labeled_scenarios = [
        (name, s) for name, s in CLINICAL_SCENARIOS.items() if name in SCENARIO_LABELS
    ]
    total = len(labeled_scenarios) * 2
    completed = 0

    for scenario_name, scenario in labeled_scenarios:
        labels = SCENARIO_LABELS[scenario_name]
        for condition in ("baseline", "treatment"):
            completed += 1
            print(f"[{completed}/{total}] {labels['id']} — {condition}...", flush=True)
            result = await run_scenario(engine, scenario_name, scenario, labels, condition)
            all_results.append(result)
            marker = "OK" if not result["error"] else "FAIL"
            print(
                f"    [{marker}] routing={result['predicted_routing_tag']} "
                f"emergency={result['emergency_score']} "
                f"routing_score={result['routing_score']} "
                f"escalation={result['escalation_score']} "
                f"({result['elapsed_ms']:.0f}ms)"
            )

    if all_results:
        fieldnames = list(all_results[0].keys())
        with RESULTS_CSV.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_results)
        print(f"\nWrote {len(all_results)} rows to {RESULTS_CSV}")
        print(f"Traces saved to {TRACES_DIR}/")

    print("\n=== TOP-LINE SUMMARY ===")
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
        print(f"\n{condition.upper()}:")
        print(f"  Routing accuracy:     {correct_routing}/{len(subset)}")
        print(f"  Escalation accuracy:  {correct_escalation}/{len(subset)}")
        if emergency_denom:
            print(f"  Emergency recall:     {emergency_num}/{emergency_denom}")


if __name__ == "__main__":
    asyncio.run(main())
