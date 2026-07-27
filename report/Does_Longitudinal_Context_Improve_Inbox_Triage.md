# Does Longitudinal Clinical Context Improve AI-Assisted Inbox Triage?

## An Evaluation of the Intercept Pipeline

Apoorva Kolhatkar
July 2026

---

## Executive Summary

I evaluate whether provider-authored longitudinal clinical context improves the routing and drafting decisions made by Intercept, an AI-assisted patient portal message triage pipeline I built at the Abridge x Anthropic x Lightspeed hackathon. I ran 12 scenarios grounded in the Abridge synthetic-ambient-fhir-25 dataset in two conditions: baseline (message only) and treatment (message plus the corresponding visit context). I scored both conditions on six dimensions covering safety, correctness, and evidence grounding.

Treatment improved escalation accuracy from 8/12 to 12/12. Emergency detection remained at 100% recall in both conditions. On the surface, this looks like a straightforward "context helps" result. It is not. Baseline reached its correct answers largely by refusing to reason without evidence and deferring to human review. Treatment reached the same answers by citing the specific documented rule that applied to the case. The routing outcomes overlap on several scenarios. The clinical quality of the reasoning does not.

The evaluation also surfaced a specific and actionable gap. The results specialist retrieves correct lab values from the retrieved context but does not attribute them to a source document. This is a one-prompt fix.

---

## Design Principles

The pipeline I evaluated operates under five design principles that are relevant to interpreting the results, particularly the observation that baseline reaches correct answers through refusal rather than through reasoning.

1. The system never diagnoses.
2. The system never overrides documented clinician reasoning.
3. Escalation is preferred over unsupported advice.
4. Every recommendation should be traceable to retrieved evidence.
5. Safety is enforced structurally, not solely through prompting.

---

## Research Question and Hypothesis

**Research question:** What effect does provider-authored longitudinal clinical context have on the routing decisions and evidence-grounded recommendations produced by an AI-assisted inbox triage system compared with message-only classification?

**Hypothesis:** Supplying the relevant provider-authored assessment and plan will improve routing accuracy and reduce unsupported clinical recommendations while preserving safety-critical emergency detection and recommendation traceability.

---

## Scope

I scoped this evaluation to the reasoning plane of the Intercept pipeline: the routing decision, the escalation decision, the drafted response when one is produced, and the evidence retrieved to justify each of these. Execution-plane concerns, including how a routing decision is written back to an EHR (UI automation, sanctioned API, or human-in-the-loop execution), are out of scope. A trustworthy reasoning plane is a prerequisite for any of those execution paths, which is why I evaluated it first.

---

## Method

### Dataset

I used 12 scenarios. Every visit context in the dataset is grounded in a real record from the Abridge-provided synthetic-ambient-fhir-25 dataset (records 6, 8, and 12). The patient portal messages are not in the source dataset, because Abridge's data consists of clinical encounters and not portal messages. I wrote every message myself, and each was written to test a specific documented detail from the corresponding record.

Two scenarios required a modification. The source dataset contains no multi-visit patients, so testing the pipeline's multi-visit context selection required combining one real visit with authored follow-through visits consistent with the patient's real documented pattern. I labeled these as HYBRID in the scenario provenance rather than presenting them as fully real.

I do not redistribute the Abridge dataset itself with this report, per dataset terms.

### Rubric

I defined and locked six dimensions before running the experiment. I made no changes to the rubric after results were available. The full rubric is included in the repository at `evaluation/rubric.md`.

1. **Emergency Detection** (binary, safety floor). Recall and precision on TRUE_EMERGENCY vs. NOT_EMERGENCY.
2. **Routing Accuracy** (categorical). Correct primary bucket assignment.
3. **Omission Rate** (categorical). Applies only to scenarios that bundle multiple requests.
4. **Escalation Appropriateness** (categorical). Correct escalation level (nurse, provider, or emergency).
5. **Unsupported Clinical Recommendations** (three-point). Whether the output's recommendations are supported by retrieved evidence.
6. **Recommendation Traceability** (three-point). Whether decisions are explicitly linked to retrieved evidence.

I do not report a composite score. Each dimension is reported independently.

### Experimental Design

For each of the 12 scenarios, I ran the pipeline twice. In the baseline condition, I stripped the visit context and the pipeline received only the patient message. In the treatment condition, the pipeline received both the message and the visit context. All other pipeline components (models, prompts, structural safety schemas, dispatch logic) were identical between conditions. This produced 24 pipeline runs and 24 trace files.

I scored mechanical dimensions (Emergency Detection, Routing Accuracy, Escalation Appropriateness) deterministically from the pipeline's structured output. I scored the two qualitative dimensions (Unsupported Clinical Recommendations, Recommendation Traceability) using Claude Sonnet 4.5 as an LLM-as-judge, applied to each trace independently. I gave the judge the pipeline output, the retrieved context status, and the rubric definition. I did not show the judge the ground-truth escalation label to avoid label leakage. Judge outputs are stored per scenario in `evaluation/judge_traces/`.

### Ground-Truth Labeling

I labeled all scenarios myself. This is a single-reviewer, self-authored evaluation. Inter-rater reliability was not measured. I state this as a limitation.

---

## Results

### Emergency Detection

Both baseline and treatment achieved 1/1 recall on the single labeled emergency scenario (ABR_003), and 0/11 false positives on the non-emergency subset. Emergency detection is uniform across conditions.

This is the intended behavior of the pipeline's Layer 0 orchestrator safety screen, which is designed to identify red-flag content in the raw message independent of visit context. The result indicates that removing context does not degrade the safety floor.

### Routing Accuracy

| | Baseline | Treatment |
|---|---|---|
| Correct routing | 11/12 | 12/12 |

Baseline correctly routed 11 of 12 scenarios to the correct primary bucket. Treatment correctly routed all 12. The single baseline miss (ABR_005, a routine refill request) was over-escalated to a clinical escalation queue rather than routed to the refill queue, which I discuss further in the Findings section.

### Escalation Appropriateness

| | Baseline | Treatment |
|---|---|---|
| Correct escalation | 8/12 | 12/12 |

Treatment reached correct escalation on all 12 scenarios. Baseline reached correct escalation on 8 of 12 and over-escalated the remaining 4. The pattern in baseline's over-escalations is the central finding of this study and I discuss it in the next section.

### Omission Rate

Only one scenario in the dataset bundled multiple requests (ABR_003). In both conditions the pipeline preserved the secondary refill request while overriding it for emergency handling. This dimension is under-represented in this dataset and I state that as a limitation.

### Unsupported Clinical Recommendations (LLM judge)

| Score | Baseline | Treatment |
|---|---|---|
| SUPPORTED | 6 | 7 |
| PARTIALLY_UNSUPPORTED | 5 | 2 |
| UNSUPPORTED_OR_CONTRADICTING | 1 | 3 |

The rise in UNSUPPORTED_OR_CONTRADICTING scores in the treatment condition is initially surprising and requires interpretation. Two of the three flagged treatment cases (ABR_010 and ABR_011) involve the results specialist retrieving correct lab values from the retrieved context but not attributing them to source documents. This is a citation gap in the specialist's output structure, not fabricated content. The third case (ABR_003 treatment) reflects a rubric edge case discussed below.

### Recommendation Traceability (LLM judge)

| Score | Baseline | Treatment |
|---|---|---|
| FULLY_TRACEABLE | 2 | 5 |
| PARTIALLY_TRACEABLE | 0 | 2 |
| NOT_TRACEABLE | 10 | 5 |

Treatment produced 5 fully traceable outputs to baseline's 2, and reduced the untraceable count from 10 to 5. The five fully traceable treatment outputs concentrate in the clinical reasoning scenarios (ABR_001, ABR_002, ABR_004, ABR_005, ABR_007), where the specialist cited direct quotes from the plan and transcript. The five untraceable treatment outputs concentrate in the administrative scenarios (billing, scheduling), where routing does not require content-specific evidence.

The two baseline outputs scored as FULLY_TRACEABLE (ABR_002 and ABR_005) reflect a known limitation of LLM-as-judge: the judge interpreted the specialist's explicit statement of context absence as an evidence-linked decision. I discuss this in Limitations.

---

## Findings

### The escalation-accuracy improvement understates what changed

Baseline reached correct escalation on 8 of 12 scenarios. This looks like a moderate baseline score. Reading the traces changed my interpretation.

On every context-dependent scenario in baseline, the specialist produced substantively identical output. It stated that no signed note was available. It stated that "silence in the note is not permission to reassure the patient." It routed to `PROVIDER_REVIEW_REQUIRED`. It did this whether the patient was within the documented monitoring threshold (ABR_001), breaching the documented threshold (ABR_002), reporting a documented adverse-effect trigger (ABR_004), or reporting a symptom persisting past a provider-specified window (ABR_007).

Baseline was not distinguishing these cases. It was uniformly deferring to human review whenever context was missing. This produced correct escalation on cases that genuinely required provider review (ABR_002, ABR_004, ABR_007) and incorrect over-escalation on cases that did not (ABR_001, ABR_005). The four failures baseline had on the escalation dimension were not random. They were the cases where the correct answer was to route to a nurse using the documented plan, and baseline could not reach that answer because it had no plan to cite.

Treatment reached correct escalation by a different mechanism. On ABR_002, the specialist produced the following justification: *"Return earlier if sleep disruption becomes nightly or mood declines" / DR: "If the four a.m. math sessions become every night, or the mood starts sinking, I want to hear about it early, not late."* It then named the matched protocol as `PROVIDER_TIMELINE_THRESHOLD` and stated that both documented criteria had now occurred. On ABR_004, the specialist quoted the plan: *"Counseled on orthostatic symptoms and cramping, instructed to call if these occur, given risk of electrolyte shifts,"* named the pattern as `PROVIDER_EXPLICIT_CALL_INSTRUCTION`, and identified that the patient was now reporting the exact symptoms the provider had asked to be called about.

The routing outcome on ABR_002 and ABR_004 is the same in both conditions: escalate to provider. The clinical quality of the reasoning is not comparable. Baseline escalates because it cannot reason. Treatment escalates because it recognizes a documented threshold that has been breached.

This may be the more important finding of this evaluation. Aggregate accuracy metrics can mask the difference between a system that is right by refusal and a system that is right by reasoning. On a small dataset, both look identical in the numbers. Only the traces distinguish them.

There are practical consequences to this pattern. On inbox triage, the cognitive work — reading the message, retrieving the patient's context, deciding who should handle it — is where most of the time goes. The physical write-back is comparatively quick. A pipeline that reaches correctness by refusing to reason therefore doesn't save the clinician much time: it defers reasoning to a human every time it lacks confidence, which is exactly when the reasoning is hardest. A pipeline that reaches correctness by grounding its decision in the plan does the reasoning work up front, leaves the clinician with a review-and-approve task, and returns the message to the correct level of the care team. Advisory execution, where Intercept recommends and the clinician clicks, preserves this division of labor. It also preserves the existing liability model: the clinician remains responsible for what the patient sees, error containment happens before the reply goes out, and every action carries a two-footprint audit trail of AI recommendation plus human decision.

### Context enables an emergent protocol taxonomy

In the treatment condition, the clinical specialist's `matched_protocol_reference` field began producing structured labels for the type of clinical rule the message engaged with. `PROVIDER_TIMELINE_THRESHOLD` for a return-window breach. `PROVIDER_EXPLICIT_CALL_INSTRUCTION` for a documented adverse-effect callback. I did not pre-specify these labels in the prompt. They emerged from the specialist's need to name the protocol pattern it was citing.

This suggests that the specialist prompt is producing an implicit taxonomy of provider-authored trigger types when it has context to work with. Formalizing this taxonomy in the prompt (or in the output schema) could improve consistency in production and would give a downstream reviewer or auditor a clean way to categorize why any given message was escalated.

### The results specialist has a source-attribution gap

Two of the three treatment cases flagged as UNSUPPORTED_OR_CONTRADICTING by the LLM judge (ABR_010 and ABR_011) involve the same failure mode. The results specialist retrieves the correct lab values from the visit context. It embeds them in the draft reply. It does not explicitly attribute them to a source document.

On ABR_010 the specialist returned "Total cholesterol: 168 mg/dL; LDL: 87 mg/dL; HDL: 59 mg/dL; Triglycerides: 110 mg/dL." These values are present in the retrieved visit note and are correct. The gap is that the draft does not say "From your annual exam on August 30, 2016." A patient reading the draft would receive accurate numbers with no way to verify which visit they are from. The LLM judge correctly flagged this as an unsupported claim under the rubric, because the values as presented are not attributed to a source.

This is a fixable gap and the fix is a single prompt change. The results specialist's prompt should require every retrieved value to be attributed to the visit date and note type from which it was drawn.

### The Layer 0 emergency screen exposes a rubric edge case

The one baseline UNSUPPORTED_OR_CONTRADICTING flag (ABR_003) and one treatment flag (ABR_003 treatment) both relate to the Layer 0 orchestrator's emergency detection. In ABR_003 the message contained overt stroke-warning content ("left arm has been completely numb since this afternoon and it's spreading up to my jaw"). The orchestrator correctly identified this as an emergency and cited `RED_FLAG_URGENCY_CRITERIA` as the matched protocol.

The judge flagged this because the retrieved context is a hypertension management note, which contains no stroke protocol. The judge's reasoning is technically correct under the rubric as I wrote it: the emergency classification is not grounded in the retrieved patient context.

The system's behavior is correct. The rubric is imprecise. Emergency detection is a safety-floor capability that must work regardless of visit context, and it should not be scored under the same "grounded in retrieved evidence" standard as clinical reasoning within a domain specialist. A production version of this rubric should distinguish safety-floor detections from evidence-grounded reasoning outputs and score them separately.

### Baseline over-escalation on routine refill

ABR_005 is a routine refill request with no reported adverse effects and a documented ongoing medication plan. Baseline routed this to `CLINICAL_ESCALATION` rather than to the refill queue. This was the single baseline routing miss.

The trace reveals the same pattern as the other baseline over-escalations: the specialist declined to route a refill without a signed note to cite, on the grounds that a missing note could hide contraindications or adverse-event triggers. Treatment routed the same scenario correctly to `REFILL_ROUTING` with an explicit citation of the medication plan.

This confirms the wider pattern. Baseline over-escalation is systematic, not random. It is a consequence of the specialist's refusal to make evidence-free routing decisions, applied uniformly whether or not the case actually requires evidence.

---

## Limitations

**Sample size.** Twelve scenarios are exploratory, not production estimates. Confidence intervals on all reported percentages are wide. The findings above are hypothesis-generating and should be replicated on a larger sample before being treated as production quality claims.

**Single-reviewer labeling.** I labeled all scenarios myself. Inter-rater reliability was not measured. A second clinician reviewing the same scenarios might disagree on difficulty tier, escalation label, or the definition of a threshold breach in the more ambiguous cases.

**Self-authored scenarios.** The patient portal messages are not from real patients. I wrote them to test specific documented details from the visit context. This means the messages test what I expected the pipeline to be tested on, which may not reflect the actual distribution of real portal messages.

**LLM-as-judge noise.** The LLM judge produced two known behaviors worth noting. First, it occasionally scored explicit acknowledgments of context absence as FULLY_TRACEABLE, on the reasoning that absence of evidence is itself documented. A stricter application of the rubric would score these NOT_TRACEABLE. Second, the judge applied the Unsupported Clinical Recommendations rubric to Layer 0 emergency detections, which produced a false positive on ABR_003 as described above. Both are addressed in Findings and neither invalidates the overall pattern of results.

**No downstream outcome data.** This evaluation measures pipeline output against my ground truth. It does not measure whether the routing decision would produce the right care experience for the patient. That measurement would require a prospective clinical evaluation with follow-up data.

**Omission and multi-request coverage.** Only one scenario in the dataset bundled multiple requests. The Omission Rate dimension is under-represented and the finding on that dimension is not robust.

---

## Future Work

Several extensions would strengthen this evaluation beyond the exploratory scope of the current study.

**Clinician-validated labels.** The single-reviewer limitation is the largest constraint on the current results. Second-reviewer scoring on the existing 12 scenarios, ideally by a licensed MD, would allow measurement of inter-rater reliability and calibration of the difficulty tiers.

**Larger and more representative sample.** A scenario set of 50 to 100 messages drawn from real de-identified portal message content would allow the findings above to be tested at meaningful statistical power. Scenario distribution should mirror the published category distribution of real portal messages rather than the balanced distribution I used here.

**Calibration analysis.** The Intercept specialists produce implicit confidence signals through their action flags. A future evaluation using a larger clinician-labeled dataset could measure whether these signals correspond to true correctness, producing a reliability diagram or Expected Calibration Error metric. I did not attempt calibration analysis here because the sample size is insufficient to support reliable calibration curves.

**Rubric refinement.** The Layer 0 emergency detection issue described in Findings suggests the rubric should distinguish safety-floor capabilities from evidence-grounded reasoning outputs. A revised rubric would score these separately.

**Prospective evaluation.** The current evaluation is retrospective on scenarios I constructed. Prospective evaluation on unseen incoming messages, with follow-up outcome data on how the routing decision affected care, would be the strongest possible test of the pipeline's clinical utility.

---

## Repository

Rubric, scenario labels, evaluation scripts, per-scenario traces, and LLM judge traces are versioned in the `evaluation/` directory of the Intercept repository. The evaluation is reproducible from `python -m evaluation.run_eval` given the Abridge synthetic-ambient-fhir-25 dataset locally.

---

*End of report.*
