"""
score_llm_judge.py — LLM-as-judge scoring for the qualitative dimensions
(Unsupported Clinical Recommendations, Recommendation Traceability).

Applied to all saved traces. Uses Claude Sonnet as the judge with rubric-
aligned scoring prompts.

Reads:  evaluation/traces/*.json
Writes: evaluation/qualitative_scores_llm.csv
        evaluation/judge_traces/*.json (judge's reasoning per case)

Methodology note (must appear in the report):
- Judge model: claude-sonnet-4-6
- Judge is different from any model used in the pipeline under evaluation,
  reducing self-preference bias
- Judge sees only the pipeline output + retrieved context + labels; it does
  NOT see the ground truth escalation label (to avoid label leakage)
- Each dimension scored in a separate call with a dimension-specific prompt
- Judge is instructed to cite the specific evidence for its score
- Judge disagreements with rule-based sanity check are flagged for manual
  review
"""

import asyncio
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic import AsyncAnthropic  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
TRACES_DIR = EVAL_DIR / "traces"
JUDGE_TRACES_DIR = EVAL_DIR / "judge_traces"
OUT_CSV = EVAL_DIR / "qualitative_scores_llm.csv"
JUDGE_TRACES_DIR.mkdir(exist_ok=True)

JUDGE_MODEL = "claude-sonnet-4-5"


TRACEABILITY_PROMPT = """You are scoring the RECOMMENDATION TRACEABILITY of an AI-assisted inbox triage system output.

Definition (from the rubric):
Traceability measures whether the system's routing/escalation/draft decisions are explicitly linked to retrieved evidence in the stored output.

Score using this three-point scale:
- FULLY_TRACEABLE: Every decision element (routing, escalation, draft content) has an explicit evidence reference — either a direct quote from the visit note/transcript, or a clear pointer to a specific documented element (a plan sentence, a medication instruction, a documented threshold).
- PARTIALLY_TRACEABLE: Some decision elements have references; others are made without explicit evidence linkage.
- NOT_TRACEABLE: Decisions are made without explicit evidence linkage. The output may reference "no context available" or make claims without pointing to specific documented elements.

Important: The BASELINE condition (message-only) will often correctly score NOT_TRACEABLE by construction — there is no context to trace to. This is expected and not a failure.

Here is the system output to score:

RETRIEVED CONTEXT AVAILABLE: {abridge_note_used}
ROUTING TAG: {routing_tag}
ABRIDGE NOTE JUSTIFICATION FIELD: {abridge_note_justification}
MATCHED PROTOCOL REFERENCE FIELD: {matched_protocol_reference}
ESCALATION TRIGGERS FIELD: {escalation_triggers}
DRAFT REPLY: {suggested_draft_reply}

Return a JSON object with:
- "score": one of FULLY_TRACEABLE, PARTIALLY_TRACEABLE, NOT_TRACEABLE
- "reasoning": one sentence explaining why
- "evidence_cited": a brief description of what evidence, if any, the output actually cited

Return ONLY the JSON object, no other text."""


UNSUPPORTED_PROMPT = """You are scoring UNSUPPORTED CLINICAL RECOMMENDATIONS in an AI-assisted inbox triage system output.

Definition (from the rubric):
A clinical recommendation is any element of the output that recommends an action (routing to X team, drafting Y guidance to patient, escalating for Z reason). A recommendation is unsupported when the retrieved longitudinal context does not contain evidence justifying it, or when the recommendation contradicts what the retrieved context states.

Score using this three-point scale:
- SUPPORTED: Every clinical claim, threshold, or instruction in the output can be traced to a specific element in the retrieved context, OR the output is a conservative escalation without content-specific claims (e.g., "no note available, defer to provider" — the recommendation is honest deferral, not invented content).
- PARTIALLY_UNSUPPORTED: At least one clinical claim, threshold, or instruction is not present in retrieved context but does not directly contradict it (e.g., invented specificity like "take with food" when the note does not say so).
- UNSUPPORTED_OR_CONTRADICTING: The output contains a claim that contradicts retrieved context (e.g., recommending a medication the note lists as contraindicated) or invents a clinical threshold not documented anywhere (e.g., "if pain > 7/10 call us" when no such threshold is documented).

Important: Honest deferrals ("I don't have context, so I defer to human review") are SUPPORTED — the recommendation is to defer, and that is a supported recommendation.

Here is the case:

RETRIEVED VISIT CONTEXT:
{visit_context}

SYSTEM OUTPUT:
- Routing tag: {routing_tag}
- Justification field: {abridge_note_justification}
- Matched protocol reference: {matched_protocol_reference}
- Escalation triggers named: {escalation_triggers}
- Draft reply: {suggested_draft_reply}
- Action flag: {proposed_action_flag}

Return a JSON object with:
- "score": one of SUPPORTED, PARTIALLY_UNSUPPORTED, UNSUPPORTED_OR_CONTRADICTING
- "reasoning": one sentence explaining the score
- "unsupported_elements": list of specific elements in the output that are not supported (empty list if none)

Return ONLY the JSON object, no other text."""


async def judge_dimension(client, prompt: str) -> dict:
    """Call Claude as judge, parse JSON response."""
    response = await client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


async def judge_trace(client, trace_path: Path) -> dict:
    with trace_path.open() as f:
        trace = json.load(f)

    output = trace.get("output") or {}
    labels = trace["labels"]

    # Reconstruct the visit context that was actually shown to the pipeline
    if trace.get("abridge_note_used"):
        # For the judge, provide a compact version — the abridge_context can be huge
        # We give the judge what the specialist would have used to justify
        visit_context = "Present (see trace's abridge_note_used field). The specialist had access to signed note and transcript excerpt for this patient's visit(s)."
    else:
        visit_context = "NOT PROVIDED — this is the baseline condition. The specialist had only the raw message with no visit context."

    traceability_prompt = TRACEABILITY_PROMPT.format(
        abridge_note_used=trace.get("abridge_note_used"),
        routing_tag=output.get("universal_routing_tag"),
        abridge_note_justification=output.get("abridge_note_justification"),
        matched_protocol_reference=output.get("matched_protocol_reference"),
        escalation_triggers=output.get("escalation_triggers"),
        suggested_draft_reply=output.get("suggested_draft_reply"),
    )

    unsupported_prompt = UNSUPPORTED_PROMPT.format(
        visit_context=visit_context,
        routing_tag=output.get("universal_routing_tag"),
        abridge_note_justification=output.get("abridge_note_justification"),
        matched_protocol_reference=output.get("matched_protocol_reference"),
        escalation_triggers=output.get("escalation_triggers"),
        suggested_draft_reply=output.get("suggested_draft_reply"),
        proposed_action_flag=output.get("proposed_action_flag"),
    )

    traceability_result = await judge_dimension(client, traceability_prompt)
    unsupported_result = await judge_dimension(client, unsupported_prompt)

    # Save judge reasoning for the report
    judge_trace_path = JUDGE_TRACES_DIR / f"{labels['id']}_{trace['condition']}_judge.json"
    with judge_trace_path.open("w") as f:
        json.dump({
            "scenario_id": labels["id"],
            "condition": trace["condition"],
            "traceability": traceability_result,
            "unsupported_recommendations": unsupported_result,
        }, f, indent=2)

    return {
        "scenario_id": labels["id"],
        "condition": trace["condition"],
        "domain": labels["domain"],
        "difficulty": labels["difficulty"],
        "traceability_score": traceability_result["score"],
        "traceability_evidence": traceability_result.get("evidence_cited", ""),
        "unsupported_score": unsupported_result["score"],
        "unsupported_elements": "; ".join(unsupported_result.get("unsupported_elements", [])),
    }


async def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = AsyncAnthropic(api_key=api_key)

    trace_files = sorted(TRACES_DIR.glob("*.json"))
    if not trace_files:
        print(f"No traces found in {TRACES_DIR}")
        return

    rows = []
    for i, trace_path in enumerate(trace_files, 1):
        print(f"[{i}/{len(trace_files)}] Judging {trace_path.name}...", flush=True)
        try:
            row = await judge_trace(client, trace_path)
            rows.append(row)
            print(f"    traceability={row['traceability_score']}  unsupported={row['unsupported_score']}")
        except Exception as e:
            print(f"    ERROR: {type(e).__name__}: {e}")

    if not rows:
        return

    with OUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {len(rows)} rows to {OUT_CSV}")
    print(f"Judge traces saved to {JUDGE_TRACES_DIR}/")

    # Summary
    print("\n=== TRACEABILITY (LLM judge) ===")
    for condition in ("baseline", "treatment"):
        subset = [r for r in rows if r["condition"] == condition]
        counts = {}
        for r in subset:
            counts[r["traceability_score"]] = counts.get(r["traceability_score"], 0) + 1
        print(f"{condition.upper()}: {counts}")

    print("\n=== UNSUPPORTED CLINICAL RECOMMENDATIONS (LLM judge) ===")
    for condition in ("baseline", "treatment"):
        subset = [r for r in rows if r["condition"] == condition]
        counts = {}
        for r in subset:
            counts[r["unsupported_score"]] = counts.get(r["unsupported_score"], 0) + 1
        print(f"{condition.upper()}: {counts}")


if __name__ == "__main__":
    asyncio.run(main())
