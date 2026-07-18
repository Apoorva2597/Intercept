from pydantic import BaseModel, Field, model_validator
from typing import Literal, Optional


# ---------------------------------------------------------------------------
# CONTEXT SELECTOR — runs when a patient has MULTIPLE prior visit notes
# available, not just one. Abridge's real Contextual Reasoning Engine spans
# a patient's whole history, not a single visit — so a message about
# something charted 3 visits ago needs a selection step before a specialist
# can reason about it, the same way a real system would need to identify
# which encounter(s) are actually relevant before citing one.
# ---------------------------------------------------------------------------
class ContextSelection(BaseModel):
    """Fast, cheap (Haiku) matching call: given a message and a list of a
    patient's prior visits, which one(s) does this message actually concern?
    This is a retrieval/matching task, not clinical judgment — the judgment
    still happens downstream in the specialist, using only the visit(s)
    selected here.
    """

    relevant_visit_dates: list[str] = Field(
        default_factory=list,
        description=(
            "The visit_date(s) from the provided list that are actually "
            "relevant to the patient's message. Usually one, but include "
            "more than one if the message plausibly touches multiple "
            "documented encounters (e.g. a recurring issue mentioned "
            "across visits). Use the exact visit_date string as given."
        ),
    )

    selection_justification: str = Field(
        description="Briefly, why these visit(s) were selected — what in the message pointed to them."
    )

    no_relevant_visit_found: bool = Field(
        default=False,
        description=(
            "True if NONE of the provided visits address what the patient "
            "is actually asking about. This is not a failure — it's the "
            "correct answer when the concern genuinely isn't documented "
            "anywhere in the provided history, and it should result in the "
            "specialist treating this as uncovered by context (escalate, "
            "don't guess), the same as a single irrelevant note would."
        ),
    )


# ---------------------------------------------------------------------------
# ORCHESTRATOR — first call, fast/cheap model (Haiku). Jobs: emergency
# screen, bucket classification, and flagging any secondary domain a
# mixed-domain message also touches so it isn't silently dropped.
# ---------------------------------------------------------------------------
class SecondaryRequest(BaseModel):
    """One bundled request outside the primary domain, with its own
    classified bucket so it can be independently dispatched and resolved —
    not just noted as a flag."""

    request: str = Field(description="One-sentence description of this bundled request.")
    bucket: Literal["CLINICAL", "REFILL", "SCHEDULING", "BILLING", "RESULTS"] = Field(
        description="Which specialist this bundled request actually belongs to. Must differ from primary_bucket."
    )


class OrchestratorClassification(BaseModel):
    """Output of the fast first-pass safety screen + bucket classifier.

    This call sees ONLY the raw message — no visit context — because its
    job is narrow: catch red flags fast, and route to the right specialist.
    If is_emergency is True, no specialist call happens at all; the
    orchestrator short-circuits directly to CLINICAL_EMERGENCY.
    """

    is_emergency: bool = Field(
        description=(
            "True if the message contains high-acuity red-flag criteria "
            "(chest pain, facial droop, signs of stroke/MI, anaphylaxis, "
            "signs of medication-specific danger such as lactic acidosis) "
            "REGARDLESS of message category or how early in a treatment "
            "timeline it arrives. Catch hedged phrasing too — 'probably "
            "nothing, but my chest feels heavy' is still a red flag; the "
            "patient's own hedge does not downgrade the symptom."
        )
    )

    primary_bucket: Literal["CLINICAL", "REFILL", "SCHEDULING", "BILLING", "RESULTS"] = Field(
        description=(
            "Best-guess top-level category from message content alone. "
            "If clinical risk is present alongside another domain, this "
            "MUST be CLINICAL — clinical risk always takes dispatch "
            "precedence. Ignored if is_emergency is True."
        )
    )

    emergency_justification: Optional[str] = Field(
        default=None,
        description="If is_emergency is True, the specific red-flag phrase that triggered it.",
    )

    secondary_requests: list[SecondaryRequest] = Field(
        default_factory=list,
        description=(
            "ALL distinct requests bundled in this message OUTSIDE the "
            "primary_bucket domain — not just the first one you notice. A "
            "message can bundle two or three asks at once (e.g. a symptom, "
            "a reschedule, AND a billing question); list every one so none "
            "are silently dropped when only the primary domain gets "
            "dispatched. Empty list if the message is single-domain."
        ),
    )

    is_staff_message: bool = Field(
        default=False,
        description=(
            "True if this message is from a staff member (a colleague "
            "physician, nurse, etc.) about a patient, rather than from the "
            "patient themselves — e.g. 'can you review Mr. Chen's chart "
            "before his 2pm.' False for anything patient-authored."
        ),
    )
    references_specific_patient: bool = Field(
        default=True,
        description=(
            "Only meaningful when is_staff_message is True. True if the "
            "staff message clearly identifies a specific patient it "
            "concerns. If a staff message does NOT reference a specific "
            "patient (e.g. a general operational question), this is "
            "explicitly out of scope for this system — it should not be "
            "forced through patient-context specialists that need a "
            "patient to reason about."
        ),
    )

    has_attachment: bool = Field(
        default=False,
        description=(
            "True if the message mentions or implies an attached image or "
            "file (e.g. 'here's a photo of my rash', 'attached is my "
            "wound picture'). This does NOT change routing severity by "
            "itself — severity is still judged from the text. It only "
            "flags that a human should actually open and look at the "
            "attachment, since this system does not visually assess "
            "clinical images."
        ),
    )


# ---------------------------------------------------------------------------
# CLINICAL / REFILL SPECIALIST — existing Layer 1 logic
# ---------------------------------------------------------------------------
class ClinicalTriageOutput(BaseModel):
    """Structured output for clinical and refill routing decisions.

    Every field is constrained to prevent the LLM from generating
    invalid routing tags or unrecognized action flags. Pydantic
    rejects any response that doesn't match these types at parse time.
    """

    primary_bucket: Literal["CLINICAL", "REFILL"]

    universal_routing_tag: Literal[
        "CLINICAL_EMERGENCY",
        "CLINICAL_ESCALATION",
        "CLINICAL_ROUTING",
        "REFILL_ROUTING",
    ] = Field(description="The routing tag that maps to a health system's local queue.")

    abridge_note_justification: str = Field(
        description=(
            "The exact quote or clinical parameter extracted from the provider's "
            "signed Abridge note that justifies this routing decision. "
            "Must trace to something the provider said and signed."
        )
    )

    matched_protocol_reference: str = Field(
        description=(
            "The triage rule or protocol logic that was matched. "
            "Examples: 'RED_FLAG_URGENCY_CRITERIA — left arm numbness with jaw involvement', "
            "'PROVIDER_TIMELINE_THRESHOLD — Day 6 exceeds 5-day window', "
            "'PROVIDER_SAFE_WINDOW — Day 3 within 5-day expected recovery'."
        )
    )

    proposed_action_flag: Literal[
        "IMMEDIATE_911_ALERT",
        "PROVIDER_REVIEW_REQUIRED",
        "NURSE_HANDLES_WITH_GUIDANCE",
        "AUTO_DRAFT_RESPONSE",
    ] = Field(description="The recommended next action for the staff member receiving this message.")

    suggested_draft_reply: Optional[str] = Field(
        default=None,
        description=(
            "A draft response the nurse can review and send, citing the provider's "
            "documented guidance. Must be None when universal_routing_tag is "
            "CLINICAL_ESCALATION or CLINICAL_EMERGENCY."
        ),
    )

    escalation_triggers: Optional[str] = Field(
        default=None,
        description="Conditions under which this routing decision should be upgraded.",
    )

    emergency_override: bool = Field(
        default=False,
        description=(
            "Defense-in-depth safety net: even though the orchestrator already "
            "screened this message, if red-flag emergency criteria are present "
            "that appear to have been missed, set this True regardless of the "
            "rest of the output."
        ),
    )
    emergency_override_reason: Optional[str] = Field(default=None)
    domain_mismatch: bool = Field(
        default=False,
        description=(
            "Set True if this message does not actually belong to this "
            "specialist's domain — e.g. the billing specialist receives a "
            "message that's actually a scheduling request with no billing "
            "content at all. This is a disagreement with the orchestrator's "
            "dispatch decision, not an emergency. Any unresolved disagreement "
            "between agents routes to human review rather than being forced "
            "into a low-confidence answer."
        ),
    )
    domain_mismatch_reason: Optional[str] = Field(default=None)

    pending_secondary_task: Optional[str] = Field(
        default=None,
        description=(
            "Populated by the orchestrator's secondary_request, not by this "
            "specialist. Preserves a bundled non-clinical request (e.g. a "
            "reschedule ask) that must not be silently dropped just because "
            "clinical risk took dispatch precedence."
        ),
    )

    attachment_flag: Optional[str] = Field(
        default=None,
        description=(
            "Do not set this field. Pipeline-populated from the "
            "orchestrator's has_attachment detection — flags that a human "
            "should open and review an attached image/file directly, since "
            "this system does not visually assess clinical images. Does "
            "not by itself change routing severity."
        ),
    )

    @model_validator(mode="after")
    def _enforce_emergency_invariants(self):
        """Structural guarantee, not just an instruction: it is impossible
        to represent an emergency with a patient-facing draft, and impossible
        for emergency_override to produce anything but the emergency state.
        """
        if self.universal_routing_tag == "CLINICAL_EMERGENCY" or self.emergency_override:
            object.__setattr__(self, "universal_routing_tag", "CLINICAL_EMERGENCY")
            object.__setattr__(self, "proposed_action_flag", "IMMEDIATE_911_ALERT")
            object.__setattr__(self, "suggested_draft_reply", None)
        if self.universal_routing_tag == "CLINICAL_ESCALATION":
            object.__setattr__(self, "suggested_draft_reply", None)
        return self


# ---------------------------------------------------------------------------
# SCHEDULING SPECIALIST — new. Task: determine real urgency + a proposed
# window + a structured action a calendar system could consume, grounded
# in what the visit note actually documented — never a guessed number.
# ---------------------------------------------------------------------------
class SchedulingTriageOutput(BaseModel):
    """Structured output for scheduling requests.

    The task is not just 'label this as scheduling' — it is to determine
    how urgently this needs a slot, grounded in documentation, and hand
    a real system a structured next step it could act on once connected.

    Covers three distinct request shapes, not just appointment booking:
    an appointment request, a check on an EXISTING referral's status, or
    a request for a NEW referral. These have different rules — a status
    check is a lookup, a new referral is a clinical decision the system
    can only forward when the chart already documents intent for it.
    """

    primary_bucket: Literal["SCHEDULING"] = "SCHEDULING"
    universal_routing_tag: Literal["SCHEDULING_ROUTING"] = "SCHEDULING_ROUTING"

    request_type: Literal["APPOINTMENT", "REFERRAL_STATUS_CHECK", "NEW_REFERRAL_REQUEST"] = Field(
        default="APPOINTMENT",
        description=(
            "APPOINTMENT: a normal scheduling/reschedule request. "
            "REFERRAL_STATUS_CHECK: 'where is my referral to X' — a lookup, "
            "answerable if real referral data is available. "
            "NEW_REFERRAL_REQUEST: 'can I get a referral to X' — a NEW "
            "clinical decision, not a lookup. Only route this forward as "
            "AUTO_CONFIRM if the visit context ALREADY documents intent "
            "for it (e.g. 'will refer to cardiology if symptoms persist') "
            "— otherwise this requires physician authorization, same "
            "'recommend, don't autonomously decide' boundary as booking."
        ),
    )

    message_was_ambiguous: bool = Field(
        default=False,
        description=(
            "True if the patient's message did NOT specify a concrete date "
            "or reference point needed to actually act on the request — "
            "e.g. 'I'm due for my annual soon' (no date given), 'I need my "
            "post-op follow-up' (no surgery date or day-count given). This "
            "is the core capability this specialist demonstrates: the "
            "message alone is often not enough to schedule correctly, and "
            "the documented visit history already has the answer."
        ),
    )
    ambiguity_resolution: Optional[str] = Field(
        default=None,
        description=(
            "Required if message_was_ambiguous is True. State exactly what "
            "the message left unspecified and what documented fact "
            "resolved it — e.g. 'Message did not give an annual-exam date; "
            "documented last annual was 2025-07-13, so due window is "
            "approximately July 2026.' If the context does NOT contain "
            "enough to resolve the ambiguity, say so explicitly and "
            "escalate rather than guessing a date."
        ),
    )

    urgency_tier: Literal["URGENT_CLINICAL_HOLD", "PRIORITY", "ROUTINE"] = Field(
        description=(
            "URGENT_CLINICAL_HOLD: the visit note flagged this follow-up as "
            "time-sensitive (e.g. post-op check, medication titration recheck) "
            "and the request touches that window. PRIORITY: not flagged urgent "
            "in the note, but the message content suggests it shouldn't wait for "
            "the next routine opening. ROUTINE: standard reschedule/booking, "
            "no documentation or content suggests urgency."
        )
    )

    context_justification: str = Field(
        description=(
            "The exact quote or documented detail from the visit context that "
            "supports the urgency tier. Preserve the provider's own language "
            "('follow up in two weeks', 'next available routine appointment') "
            "rather than converting it into invented numeric precision the "
            "note doesn't actually support."
        )
    )

    proposed_window: str = Field(
        description=(
            "A grounded scheduling window, stated in the provider's own "
            "documented terms where possible (e.g. 'within 1 week — note "
            "flagged 2-week post-op recheck'), not an arbitrary guess or a "
            "false-precision number. If the note gives no timeline, say "
            "'no documented timeline — defer to standard scheduling policy'."
        )
    )

    proposed_action_flag: Literal[
        "FLAG_FOR_SCHEDULING_TEAM_HIGH_PRIORITY",
        "AUTO_CONFIRM_ROUTINE_REQUEST",
        "PROVIDER_REVIEW_REQUIRED",
    ] = Field(description="The structured next step — this is what a connected calendar system would consume.")

    suggested_draft_reply: Optional[str] = Field(default=None)

    emergency_override: bool = Field(
        default=False,
        description=(
            "Defense-in-depth safety net: if red-flag emergency criteria are "
            "present in the message that appear to have been missed by the "
            "safety screen, set this True — a scheduling request is not a "
            "reason to ignore an emergency mentioned in passing."
        ),
    )
    emergency_override_reason: Optional[str] = Field(default=None)
    domain_mismatch: bool = Field(
        default=False,
        description=(
            "Set True if this message does not actually belong to this "
            "specialist's domain — e.g. the billing specialist receives a "
            "message that's actually a scheduling request with no billing "
            "content at all. This is a disagreement with the orchestrator's "
            "dispatch decision, not an emergency. Any unresolved disagreement "
            "between agents routes to human review rather than being forced "
            "into a low-confidence answer."
        ),
    )
    domain_mismatch_reason: Optional[str] = Field(default=None)

    pending_secondary_task: Optional[str] = Field(
        default=None,
        description="Do not set this field. It is overwritten by the pipeline after your response and any value you provide here is discarded.",
    )
    attachment_flag: Optional[str] = Field(
        default=None,
        description="Do not set this field. Pipeline-populated from orchestrator attachment detection.",
    )

    @model_validator(mode="after")
    def _enforce_override_invariants(self):
        if self.emergency_override:
            object.__setattr__(self, "suggested_draft_reply", None)
            object.__setattr__(self, "proposed_action_flag", "PROVIDER_REVIEW_REQUIRED")
        return self


# ---------------------------------------------------------------------------
# BILLING SPECIALIST — new. Task: classify the billing question correctly
# and NEVER fabricate a dollar figure or specific charge — that is the
# single highest-risk hallucination surface in this domain.
# ---------------------------------------------------------------------------
class ResultsTriageOutput(BaseModel):
    """Structured output for lab/imaging result questions.

    Splits into three genuinely different tasks with different rules —
    conflating them was the actual risk here:
    1. 'Send me my results' — data retrieval, gated on release status.
    2. 'Why are they off' — INTERPRETATION. Forbidden, same category as
       diagnosing or recommending a medication change. State the fact
       (value, reference range, flagged high/low) — never explain what
       it clinically means.
    3. 'Do I need a new appointment' — a threshold comparison, same
       pattern as the clinical specialist's timeline logic: only answer
       if the chart documents a real threshold to compare against.
    """

    primary_bucket: Literal["RESULTS"] = "RESULTS"
    universal_routing_tag: Literal["RESULTS_ROUTING", "RESULTS_ESCALATION"] = Field(
        description="RESULTS_ROUTING for routine/released results; RESULTS_ESCALATION for anything unreleased, abnormal without a documented plan, or an interpretation request."
    )

    question_type: Literal["RETRIEVE_RESULT", "INTERPRETATION_REQUESTED", "APPOINTMENT_NEED_CHECK"] = Field(
        description=(
            "RETRIEVE_RESULT: patient wants the actual value — answerable "
            "ONLY if the result is marked released to the patient. "
            "INTERPRETATION_REQUESTED: patient is asking what a result "
            "MEANS — this agent never answers this, full stop, always "
            "escalates. APPOINTMENT_NEED_CHECK: whether an abnormal result "
            "needs a new visit — only answerable against a real documented "
            "threshold, never inferred."
        )
    )

    result_released: bool = Field(
        default=False,
        description="Do not assert True without real data confirming release status. Unreleased results are never surfaced to the patient, regardless of how routine they look."
    )

    message_was_ambiguous: bool = Field(
        default=False,
        description=(
            "True if the message didn't specify WHICH result — 'send me my "
            "results' with no test named, when the documented visit "
            "context makes clear which result is actually in question. "
            "This is the core capability: the message alone is often not "
            "specific enough, and the documented visit already answers it."
        ),
    )
    ambiguity_resolution: Optional[str] = Field(
        default=None,
        description=(
            "Required if message_was_ambiguous is True. State what was "
            "unspecified and what documented detail resolved it. If "
            "multiple results exist and context doesn't clarify which one "
            "the patient means, say so explicitly and escalate rather "
            "than guessing which result to surface."
        ),
    )

    context_justification: str = Field(
        description="The exact quote or documented detail (a stated threshold, a release status) supporting this decision."
    )

    proposed_action_flag: Literal[
        "AUTO_DRAFT_RESPONSE", "PROVIDER_REVIEW_REQUIRED", "NURSE_HANDLES_WITH_GUIDANCE"
    ]

    suggested_draft_reply: Optional[str] = Field(
        default=None,
        description="Must be None whenever question_type is INTERPRETATION_REQUESTED, or when result_released is False."
    )

    emergency_override: bool = Field(default=False)
    emergency_override_reason: Optional[str] = Field(default=None)
    domain_mismatch: bool = Field(default=False)
    domain_mismatch_reason: Optional[str] = Field(default=None)
    pending_secondary_task: Optional[str] = Field(default=None, description="Do not set this field. Pipeline-populated.")
    attachment_flag: Optional[str] = Field(default=None, description="Do not set this field. Pipeline-populated.")

    @model_validator(mode="after")
    def _enforce_results_invariants(self):
        """Structural guarantee, not just an instruction: interpretation
        requests and unreleased results can never carry a draft reply,
        regardless of what the model tried to output."""
        if self.question_type == "INTERPRETATION_REQUESTED":
            object.__setattr__(self, "suggested_draft_reply", None)
            object.__setattr__(self, "universal_routing_tag", "RESULTS_ESCALATION")
            object.__setattr__(self, "proposed_action_flag", "PROVIDER_REVIEW_REQUIRED")
        if not self.result_released and self.question_type == "RETRIEVE_RESULT":
            object.__setattr__(self, "suggested_draft_reply", None)
            object.__setattr__(self, "universal_routing_tag", "RESULTS_ESCALATION")
        if self.emergency_override:
            object.__setattr__(self, "suggested_draft_reply", None)
            object.__setattr__(self, "proposed_action_flag", "PROVIDER_REVIEW_REQUIRED")
        return self


class BillingTriageOutput(BaseModel):
    """Structured output for billing requests.

    Hard rule: this agent must never invent a specific dollar amount,
    balance, or charge. If the message requires looking up a real number
    the agent doesn't have, it must say so and escalate — not approximate.

    Structural note: there is deliberately no `estimated_amount` field
    anywhere in this schema. If a trusted billing tool is connected in the
    future, that would be a new, explicitly-sourced field — this schema
    should never grow a free-text or numeric dollar field the model could
    fill in on its own.
    """

    primary_bucket: Literal["BILLING"] = "BILLING"
    universal_routing_tag: Literal["BILLING_ROUTING", "BILLING_DISPUTE"]

    question_type: Literal[
        "GENERAL_POLICY_QUESTION",
        "SPECIFIC_CHARGE_LOOKUP_NEEDED",
        "DISPUTE",
    ] = Field(
        description=(
            "GENERAL_POLICY_QUESTION: answerable from general billing policy, "
            "no real account lookup needed. SPECIFIC_CHARGE_LOOKUP_NEEDED: "
            "requires a real number the agent does not have — must not be "
            "answered, only routed. DISPUTE: patient is contesting a charge."
        )
    )

    coverage_status: Literal[
        "UNKNOWN", "REQUIRES_PAYER_VERIFICATION", "CONFIRMED_COVERED", "CONFIRMED_NOT_COVERED"
    ] = Field(
        default="UNKNOWN",
        description=(
            "CONFIRMED_COVERED/CONFIRMED_NOT_COVERED may ONLY be set when "
            "grounded_in_real_data is also True — the validator forces this "
            "back to UNKNOWN otherwise, regardless of how confident the "
            "reasoning sounds. Structurally, a coverage claim without real "
            "data behind it is impossible to represent."
        ),
    )

    grounded_in_real_data: bool = Field(
        default=False,
        description=(
            "Set True ONLY if your answer is based on real billing/coverage "
            "data explicitly provided in the context (labeled 'Real billing "
            "data'), not your own reasoning or general knowledge. This is "
            "what the validator checks before allowing a confirmed coverage "
            "or charge claim to stand."
        ),
    )

    message_was_ambiguous: bool = Field(
        default=False,
        description=(
            "True if the message didn't specify WHICH visit or charge — "
            "'I got charged for something, not sure what' — when the "
            "documented visit context makes clear what it was actually "
            "for. This is the core capability: confirming WHAT the charge "
            "concerns from context, never inventing the dollar amount "
            "itself — those stay two separate things."
        ),
    )
    ambiguity_resolution: Optional[str] = Field(
        default=None,
        description=(
            "Required if message_was_ambiguous is True. State what was "
            "unspecified and what documented visit detail resolved it. "
            "If context doesn't clarify which visit/charge is meant, say "
            "so explicitly rather than guessing."
        ),
    )

    context_justification: str = Field(
        description="What in the message or context supports this classification."
    )

    proposed_action_flag: Literal[
        "AUTO_DRAFT_RESPONSE",
        "ESCALATE_TO_BILLING_TEAM",
        "ESCALATE_TO_CODING_COMPLIANCE",
    ]

    hard_rule_triggered: bool = Field(
        description=(
            "True if this message asked for a specific dollar amount, balance, "
            "coverage determination, refund, or charge-correctness verdict the "
            "agent does not actually have — confirms the agent escalated "
            "instead of fabricating or asserting one."
        )
    )

    suggested_draft_reply: Optional[str] = Field(
        default=None,
        description="Must be None if hard_rule_triggered is True — no draft reply that could imply a real number or coverage determination.",
    )

    emergency_override: bool = Field(
        default=False,
        description=(
            "Defense-in-depth safety net: if red-flag emergency criteria are "
            "present in the message that appear to have been missed by the "
            "safety screen, set this True — a billing question is not a "
            "reason to ignore an emergency mentioned in passing."
        ),
    )
    emergency_override_reason: Optional[str] = Field(default=None)
    domain_mismatch: bool = Field(
        default=False,
        description=(
            "Set True if this message does not actually belong to this "
            "specialist's domain — e.g. the billing specialist receives a "
            "message that's actually a scheduling request with no billing "
            "content at all. This is a disagreement with the orchestrator's "
            "dispatch decision, not an emergency. Any unresolved disagreement "
            "between agents routes to human review rather than being forced "
            "into a low-confidence answer."
        ),
    )
    domain_mismatch_reason: Optional[str] = Field(default=None)

    pending_secondary_task: Optional[str] = Field(
        default=None,
        description="Do not set this field. It is overwritten by the pipeline after your response and any value you provide here is discarded.",
    )
    attachment_flag: Optional[str] = Field(
        default=None,
        description="Do not set this field. Pipeline-populated from orchestrator attachment detection.",
    )

    @model_validator(mode="after")
    def _enforce_hard_rule_invariants(self):
        """Structural guarantee: hard_rule_triggered and emergency_override
        cannot coexist with a draft reply or an asserted coverage status,
        regardless of what the model tried to put in those fields. A
        CONFIRMED coverage status is only valid when grounded_in_real_data
        is True — this makes an ungrounded confirmed claim structurally
        impossible, not just discouraged.
        """
        if self.coverage_status in ("CONFIRMED_COVERED", "CONFIRMED_NOT_COVERED") and not self.grounded_in_real_data:
            object.__setattr__(self, "coverage_status", "UNKNOWN")
        if self.hard_rule_triggered:
            object.__setattr__(self, "suggested_draft_reply", None)
            object.__setattr__(self, "coverage_status", "REQUIRES_PAYER_VERIFICATION")
        if self.emergency_override:
            object.__setattr__(self, "suggested_draft_reply", None)
            object.__setattr__(self, "proposed_action_flag", "ESCALATE_TO_BILLING_TEAM")
        return self
