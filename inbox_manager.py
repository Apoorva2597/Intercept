"""
Inbox-level state management for Abridge Intercept.

Everything in pipeline.py reasons about ONE message at a time, statelessly.
This module adds the piece that was missing to honestly call this "inbox
management" rather than "message triage": memory across messages from the
same patient, so a pattern of repeated, unresolved contact about the same
issue can itself become a signal — even when no single message in the
pattern crosses a threshold on its own — plus prioritization across a batch
of simultaneously open messages from different patients.

Deliberately kept DETERMINISTIC, layered on top of
InterceptEngine.process_message, not a new LLM call. Pattern detection
(same patient, same bucket, repeated non-resolution) doesn't need
judgment, it needs counting — so it's implemented as counting. That keeps
it fast, free, and fully auditable: no model call to second-guess, no new
place for a hallucination to hide.

Honest limitation: history is in-memory and resets when the process
restarts. A production version needs durable, patient-identified storage —
this proves the mechanism, not a deployable data layer.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

# Routine, non-escalated outcomes — the set that a repeated-contact pattern
# can upgrade out of. Emergency/escalation/dispute tags are already the
# most urgent tier and are left untouched by this logic.
NON_ESCALATED_TAGS = {"CLINICAL_ROUTING", "REFILL_ROUTING", "SCHEDULING_ROUTING", "BILLING_ROUTING"}

# Priority ranking for sorting a batch of inbox messages, most urgent
# first. Lower number = higher priority. This is intentionally a function,
# not a flat dict lookup — urgency_tier (scheduling) and universal_routing_tag
# (everything else) are different fields on different output types, and a
# flat dict-only approach let URGENT_CLINICAL_HOLD tie with routine clinical
# outcomes instead of correctly outranking them. Caught by testing, not
# assumed correct.
def _priority_score(output) -> int:
    tag = getattr(output, "universal_routing_tag", None)
    urgency = getattr(output, "urgency_tier", None)
    action = getattr(output, "proposed_action_flag", None)

    if tag == "CLINICAL_EMERGENCY":
        return 0
    if tag == "CLINICAL_ESCALATION":
        return 1
    if urgency == "URGENT_CLINICAL_HOLD":
        return 2
    if tag == "BILLING_DISPUTE":
        return 3
    if urgency == "PRIORITY":
        return 3
    # Routine tier — differentiate by category, with a small nudge for
    # action flags that mean "needs a human sooner" within that tier.
    base = {
        "CLINICAL_ROUTING": 4,
        "REFILL_ROUTING": 5,
        "SCHEDULING_ROUTING": 5,
        "BILLING_ROUTING": 6,
    }.get(tag, 9)
    boost = {
        "PROVIDER_REVIEW_REQUIRED": -1,
        "ESCALATE_TO_BILLING_TEAM": -1,
        "ESCALATE_TO_CODING_COMPLIANCE": -1,
    }.get(action, 0)
    return base + boost


@dataclass
class ContactRecord:
    timestamp: datetime
    bucket: str
    routing_tag: str
    message_snippet: str


def _already_escalated(output) -> bool:
    """True if this output is already at or above the urgency tier that
    repeated-contact escalation would push it to — don't escalate what's
    already escalated."""
    tag = getattr(output, "universal_routing_tag", None)
    if tag in ("CLINICAL_EMERGENCY", "CLINICAL_ESCALATION", "BILLING_DISPUTE"):
        return True
    if getattr(output, "urgency_tier", None) == "URGENT_CLINICAL_HOLD":
        return True
    return False


class InboxManager:
    """Wraps InterceptEngine with per-patient history, repeated-contact
    escalation, and batch prioritization across many open messages — the
    actual "manage the inbox" layer on top of per-message triage.

    History persistence: pass db_path to back self.history with SQLite so
    it survives a process restart. Without db_path, history is in-memory
    only and resets on restart — fine for a demo, not for anything real.
    This is a genuine fix, not a bigger claim than it is: it's a single
    local SQLite file, not a production data store with concurrent-write
    handling, backups, or patient-identity resolution across systems —
    those are real infrastructure work a production deployment still needs.
    """

    def __init__(self, engine, repeated_contact_threshold: int = 2, window_days: int = 7, db_path: str = None):
        """repeated_contact_threshold and window_days are configurable
        starting points, not validated clinical constants. I looked for a
        published numeric standard for "how many contacts before this
        should escalate" and did not find one — real telephone triage
        tooling (e.g. Schmitt-Thompson-based systems) explicitly treats
        protocols as "the floor, not the ceiling": a nurse is expected to
        escalate on judgment even when no rule technically fires, precisely
        because repeated unresolved contact is a recognized informal
        red flag, not a codified threshold. These defaults encode that
        same judgment as a rule so the system has SOME mechanism instead of
        none — they should be reviewed and tuned by clinical staff before
        any real deployment, not treated as evidence-based as they stand.
        """
        self.engine = engine
        self.history: dict[str, list[ContactRecord]] = {}
        self.repeated_contact_threshold = repeated_contact_threshold
        self.window_days = window_days
        self.db_path = db_path
        self._db = None
        if db_path:
            self._init_db()

    def _init_db(self) -> None:
        import sqlite3
        self._db = sqlite3.connect(self.db_path)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS contact_history (
                patient_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                bucket TEXT,
                routing_tag TEXT,
                message_snippet TEXT
            )
        """)
        self._db.commit()
        # Load existing history into memory so check_repeated_contact
        # doesn't need to hit the DB on every call.
        for patient_id, ts, bucket, tag, snippet in self._db.execute(
            "SELECT patient_id, timestamp, bucket, routing_tag, message_snippet FROM contact_history"
        ):
            self.history.setdefault(patient_id, []).append(
                ContactRecord(timestamp=datetime.fromisoformat(ts), bucket=bucket, routing_tag=tag, message_snippet=snippet)
            )

    def _persist_record(self, patient_id: str, record: ContactRecord) -> None:
        if not self._db:
            return
        self._db.execute(
            "INSERT INTO contact_history (patient_id, timestamp, bucket, routing_tag, message_snippet) VALUES (?, ?, ?, ?, ?)",
            (patient_id, record.timestamp.isoformat(), record.bucket, record.routing_tag, record.message_snippet),
        )
        self._db.commit()

    def _recent_records(self, patient_id: str, bucket: str, now: datetime) -> list:
        records = self.history.get(patient_id, [])
        cutoff = now - timedelta(days=self.window_days)
        return [r for r in records if r.bucket == bucket and r.timestamp >= cutoff]

    def check_repeated_contact(self, patient_id: str, bucket: str, now: datetime = None):
        """Returns (pattern_detected, prior_non_escalated_count). No model
        call — pure counting against recorded history."""
        now = now or datetime.now()
        recent = self._recent_records(patient_id, bucket, now)
        non_escalated_recent = [r for r in recent if r.routing_tag in NON_ESCALATED_TAGS]
        return len(non_escalated_recent) >= self.repeated_contact_threshold, len(non_escalated_recent)

    @staticmethod
    def _escalate_for_repeated_contact(output, prior_count: int):
        """Upgrade a routine outcome because the PATTERN of repeated,
        unresolved contact is itself a signal — even though this message
        alone wouldn't have escalated. Emergency/hard-rule structural
        invariants from models.py are untouched; this only touches the
        routine tier."""
        note = (
            f" [ESCALATED: {prior_count} prior unresolved contact(s) about "
            f"this in the recent window — repeated-contact pattern, not a "
            f"single-message decision]"
        )
        cls_name = type(output).__name__
        if cls_name == "ClinicalTriageOutput":
            return output.model_copy(update={
                "universal_routing_tag": "CLINICAL_ESCALATION",
                "proposed_action_flag": "PROVIDER_REVIEW_REQUIRED",
                "suggested_draft_reply": None,
                "matched_protocol_reference": output.matched_protocol_reference + note,
            })
        if cls_name == "SchedulingTriageOutput":
            return output.model_copy(update={
                "urgency_tier": "PRIORITY",
                "proposed_action_flag": "PROVIDER_REVIEW_REQUIRED",
                "suggested_draft_reply": None,
                "context_justification": output.context_justification + note,
            })
        if cls_name == "BillingTriageOutput":
            return output.model_copy(update={
                "proposed_action_flag": "ESCALATE_TO_BILLING_TEAM",
                "suggested_draft_reply": None,
                "context_justification": output.context_justification + note,
            })
        return output

    async def process_inbox_message(self, patient_id: str, message_text: str, abridge_note=None, timestamp=None):
        """Wraps engine.process_message with repeated-contact awareness.
        Same (specialist_name, output, diagnostics) return shape, plus
        diagnostics['repeated_contact'] describing whether a pattern was
        detected and whether it changed the outcome.
        """
        now = timestamp or datetime.now()
        specialist_name, output, diagnostics = await self.engine.process_message(message_text, abridge_note)

        bucket = getattr(output, "primary_bucket", None)
        pattern_detected, prior_count = self.check_repeated_contact(patient_id, bucket, now)

        diagnostics["repeated_contact"] = {
            "pattern_detected": pattern_detected,
            "prior_contact_count": prior_count,
            "window_days": self.window_days,
            "escalation_applied": False,
        }

        if pattern_detected and not _already_escalated(output):
            output = self._escalate_for_repeated_contact(output, prior_count)
            specialist_name = f"{specialist_name} (escalated: repeated contact pattern)"
            diagnostics["repeated_contact"]["escalation_applied"] = True

        final_tag = getattr(output, "universal_routing_tag", None)
        record = ContactRecord(timestamp=now, bucket=bucket, routing_tag=final_tag, message_snippet=message_text[:100])
        self.history.setdefault(patient_id, []).append(record)
        self._persist_record(patient_id, record)

        return specialist_name, output, diagnostics

    async def prioritize_queue(self, messages: list) -> list:
        """messages: list of dicts with patient_id, message_text,
        abridge_note (optional), timestamp (optional). Runs each through
        process_inbox_message and returns them sorted most-urgent-first —
        this is the actual inbox view: many open messages, ranked, not one
        message triaged in isolation.

        Concurrency note: messages are grouped by patient and each
        patient's OWN messages are processed strictly in order — repeated-
        contact detection reads and writes that patient's history, so
        running a patient's messages out of order (or concurrently with
        each other) would corrupt the pattern count. Different patients'
        groups have no shared state, so those groups run concurrently with
        each other. This is a correctness constraint, not a missed
        optimization — full parallelism across every message would be
        faster but wrong.
        """
        by_patient: dict[str, list] = {}
        for m in messages:
            by_patient.setdefault(m["patient_id"], []).append(m)

        async def process_patient_group(patient_id: str, group: list) -> list:
            group_results = []
            for m in group:
                specialist, output, diagnostics = await self.process_inbox_message(
                    patient_id, m["message_text"], m.get("abridge_note"), m.get("timestamp")
                )
                group_results.append({
                    "patient_id": patient_id,
                    "message_text": m["message_text"],
                    "specialist": specialist,
                    "output": output,
                    "diagnostics": diagnostics,
                    "priority_score": _priority_score(output),
                })
            return group_results

        group_outcomes = await asyncio.gather(*[
            process_patient_group(pid, group) for pid, group in by_patient.items()
        ])
        results = [item for group in group_outcomes for item in group]
        results.sort(key=lambda r: r["priority_score"])
        return results
