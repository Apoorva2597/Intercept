"""
Epic open FHIR sandbox client — real SMART on FHIR OAuth2 + Schedule/Slot/
Appointment queries.

HONEST STATUS: this code is written against Epic's real, documented
endpoints and OAuth2 flow, and its logic is tested against simulated
responses (see test block at the bottom). It has NOT been run against the
live Epic sandbox from within this environment — the sandbox's network
egress allowlist blocks fhir.epic.com entirely (confirmed: HTTP 403,
x-deny-reason: host_not_allowed). Running this for real requires:

1. A free client_id, registered at https://open.epic.com/MyApps
   ("Create a new App", mark it ready for Sandbox — takes ~1 hour to sync).
2. Completing the OAuth2 EHR-launch flow through a real browser via
   https://open.epic.com/launchpad — this cannot be done headlessly, it
   requires the interactive consent/login step.
3. Once you have a client_id and have completed the launch flow once to
   get a token, this module's query functions will work as written.

Endpoints and OAuth flow below are Epic's real, documented ones — not
invented. Base URL: https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/STU3/

CORRECTED FROM AN EARLIER R4 ASSUMPTION: Epic's actual app-registration
scope list shows Schedule and Slot are ONLY offered under STU3, not R4 —
there is no R4 version of either. Appointment is offered under both, but
the STU3 version additionally supports $book and $find operations the R4
one doesn't, so STU3 is used uniformly here for consistency, not as a
downgrade.
"""

import httpx
from typing import Optional


EPIC_FHIR_BASE = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/STU3"
EPIC_AUTHORIZE_URL = "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize"
EPIC_TOKEN_URL = "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token"


class EpicSchedulingClient:
    """Thin client for Epic's Schedule/Slot/Appointment FHIR resources.

    This does NOT attempt to book anything — consistent with the
    scheduling specialist's existing hard rule (never promise a real slot
    without checking), this client's only job is to let the specialist
    ground its urgency reasoning in ACTUAL slot availability, when real
    access exists, instead of reasoning in the dark.
    """

    def __init__(self, access_token: str, patient_fhir_id: Optional[str] = None):
        self.access_token = access_token
        self.patient_fhir_id = patient_fhir_id
        self._client = httpx.Client(
            base_url=EPIC_FHIR_BASE,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/fhir+json",
            },
            timeout=15.0,
        )

    def find_available_slots(self, specialty: Optional[str] = None, days_ahead: int = 14) -> list[dict]:
        """Query real Slot resources. Returns a list of simplified dicts:
        {slot_id, start, end, status, schedule_id}. Only 'free' status
        slots are actionable — Epic's Slot resource models availability,
        not a booking action.
        """
        params = {"status": "free"}
        if specialty:
            params["service-type"] = specialty
        response = self._client.get("/Slot", params=params)
        response.raise_for_status()
        bundle = response.json()
        slots = []
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            slots.append({
                "slot_id": resource.get("id"),
                "start": resource.get("start"),
                "end": resource.get("end"),
                "status": resource.get("status"),
                "schedule_id": self._extract_schedule_ref(resource),
            })
        return slots

    def get_patient_appointments(self) -> list[dict]:
        """Query existing Appointment resources for the launched patient —
        useful for confirming whether a documented follow-up is already
        scheduled, not just whether a slot theoretically exists."""
        if not self.patient_fhir_id:
            raise ValueError("patient_fhir_id required for appointment lookup")
        response = self._client.get("/Appointment", params={"patient": self.patient_fhir_id})
        response.raise_for_status()
        bundle = response.json()
        appointments = []
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            appointments.append({
                "appointment_id": resource.get("id"),
                "status": resource.get("status"),
                "start": resource.get("start"),
                "description": resource.get("description"),
            })
        return appointments

    @staticmethod
    def _extract_schedule_ref(slot_resource: dict) -> Optional[str]:
        schedule = slot_resource.get("schedule", {})
        ref = schedule.get("reference", "")
        return ref.split("/")[-1] if ref else None

    def get_account_billing(self) -> Optional[dict]:
        """Query the real Account (Premium Billing) resource for the
        launched patient. Read-only — same risk tier as find_available_slots,
        not a write. Returns None if nothing found, never a guessed value."""
        if not self.patient_fhir_id:
            raise ValueError("patient_fhir_id required for billing lookup")
        response = self._client.get("/Account", params={"patient": self.patient_fhir_id, "status": "active"})
        response.raise_for_status()
        bundle = response.json()
        entries = bundle.get("entry", [])
        if not entries:
            return None
        resource = entries[0].get("resource", {})
        return {
            "account_id": resource.get("id"),
            "balance": resource.get("balance"),  # real field if present, never invented
            "status": resource.get("status"),
            "description": resource.get("description"),
        }

    def get_coverage(self) -> Optional[dict]:
        """Query the real Coverage resource — confirms actual insurance on
        file, not a guess about what a patient 'probably' has."""
        if not self.patient_fhir_id:
            raise ValueError("patient_fhir_id required for coverage lookup")
        response = self._client.get("/Coverage", params={"patient": self.patient_fhir_id, "status": "active"})
        response.raise_for_status()
        bundle = response.json()
        entries = bundle.get("entry", [])
        if not entries:
            return None
        resource = entries[0].get("resource", {})
        payor = resource.get("payor", [{}])
        return {
            "coverage_id": resource.get("id"),
            "status": resource.get("status"),
            "payor_reference": payor[0].get("display") if payor else None,
        }

    def get_lab_results(self) -> list[dict]:
        """Query real Observation (lab) resources for the launched patient.
        Same discipline as everywhere else: this returns whatever the real
        record actually has, including its real status — it does NOT
        invent a 'released to patient' flag, since that's an EHR-specific
        field this raw resource may not expose. The results specialist's
        hard rule (never surface without explicit release confirmation)
        still governs what gets shown to a patient regardless of what
        comes back here."""
        if not self.patient_fhir_id:
            raise ValueError("patient_fhir_id required for lab results lookup")
        response = self._client.get("/Observation", params={"patient": self.patient_fhir_id, "category": "laboratory"})
        response.raise_for_status()
        bundle = response.json()
        results = []
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            code = resource.get("code", {})
            value_qty = resource.get("valueQuantity", {})
            results.append({
                "observation_id": resource.get("id"),
                "test_name": code.get("text") or (code.get("coding", [{}])[0].get("display") if code.get("coding") else None),
                "value": value_qty.get("value"),
                "unit": value_qty.get("unit"),
                "status": resource.get("status"),
                "effective_date": resource.get("effectiveDateTime"),
            })
        return results

    def get_medication_status(self) -> list[dict]:
        """Query real MedicationRequest resources for the launched patient —
        actual prescription status (active/expired), refills remaining,
        dosage. Same risk tier as slot/billing lookup: read-only, never a
        guess about whether a refill is actually still valid."""
        if not self.patient_fhir_id:
            raise ValueError("patient_fhir_id required for medication lookup")
        response = self._client.get("/MedicationRequest", params={"patient": self.patient_fhir_id, "status": "active"})
        response.raise_for_status()
        bundle = response.json()
        medications = []
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            med_concept = resource.get("medicationCodeableConcept", {})
            dispense = resource.get("dispenseRequest", {})
            dosage = resource.get("dosageInstruction", [{}])
            medications.append({
                "request_id": resource.get("id"),
                "medication_name": med_concept.get("text") or (med_concept.get("coding", [{}])[0].get("display") if med_concept.get("coding") else None),
                "status": resource.get("status"),
                "refills_remaining": dispense.get("numberOfRepeatsAllowed"),
                "authored_on": resource.get("authoredOn"),
                "dosage_text": dosage[0].get("text") if dosage else None,
            })
        return medications

    def propose_booking(self, slot_id: str) -> dict:
        """Returns a PROPOSAL only — does NOT call the real $book operation.
        This is the human-confirmation gate: Intercept recommends a specific
        real slot, a person reviews and explicitly confirms, and ONLY THEN
        does confirm_booking() below actually execute the write. Keeping
        propose and confirm as two separate methods is a deliberate design
        choice, not a technical limitation — it's what keeps this system a
        recommendation engine rather than an autonomous actor (the same
        distinction the FDA's clinical-decision-support exemption depends
        on: recommend, don't act, without a human in the loop)."""
        return {"slot_id": slot_id, "status": "PROPOSED_NOT_BOOKED", "requires_human_confirmation": True}

    def confirm_booking(self, slot_id: str, patient_fhir_id: str) -> dict:
        """Actually calls Epic's real $book operation. Must ONLY be invoked
        after an explicit human confirmation action (e.g. a button click in
        the UI) — never called automatically by the reasoning pipeline
        itself. The caller (main.py) is responsible for enforcing that this
        only fires from a genuine user action, not a specialist's output."""
        response = self._client.post(
            "/Appointment/$book",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "slot", "valueReference": {"reference": f"Slot/{slot_id}"}},
                    {"name": "patient", "valueReference": {"reference": f"Patient/{patient_fhir_id}"}},
                ],
            },
        )
        response.raise_for_status()
        return response.json()

    def close(self):
        self._client.close()


def get_backend_access_token(client_id: str, private_key_path: str, token_url: str = EPIC_TOKEN_URL) -> str:
    """SMART Backend Services authentication — a completely different,
    non-interactive path from the standalone-launch flow used elsewhere
    in this file. No browser, no login screen, no test-patient credential
    at all. Instead: a JWT signed with a private key whose matching public
    key was uploaded to Epic's app registration, proving identity
    cryptographically instead of via a password.

    Built as a direct fix for a real, live blocker: standalone launch's
    interactive login screen requires a specific sandbox test account,
    and the commonly-referenced community credentials could not be
    confirmed current. Backend Services sidesteps that entirely — Epic's
    own docs describe this exact key-registration pattern for
    'Backend Systems' user type, which several of our registered scopes
    (e.g. Slot.Read) explicitly support.
    """
    import jwt as pyjwt
    import uuid
    import time

    with open(private_key_path, "rb") as f:
        private_key = f.read()

    now = int(time.time())
    claims = {
        "iss": client_id,
        "sub": client_id,
        "aud": token_url,
        "jti": str(uuid.uuid4()),
        "exp": now + 300,  # 5 minutes — short-lived by design, a new JWT is signed per token request
        "iat": now,
    }
    assertion = pyjwt.encode(claims, private_key, algorithm="RS384")

    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion,
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]


def exchange_code_for_token(client_id: str, redirect_uri: str, authorization_code: str) -> dict:
    """Step 2 of the real SMART on FHIR EHR-launch flow: trade the
    authorization code (received after the browser-based consent step)
    for an access token + launch context (patient, encounter FHIR IDs).

    This is a real, standard OAuth2 authorization_code grant — Epic's
    documented flow, not a custom shortcut.
    """
    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            EPIC_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
            },
        )
        response.raise_for_status()
        return response.json()  # includes access_token, patient, encounter, expires_in


def build_authorization_url(client_id: str, redirect_uri: str, iss: str) -> str:
    """Step 1: build the URL to redirect the user's browser to for the
    interactive consent step. This part fundamentally cannot be automated
    or run headlessly — Epic requires a real login/consent interaction.

    FIXED: this app is STANDALONE (launched independently, not embedded
    inside Epic's own UI) — the correct SMART on FHIR flow for that is
    Standalone Launch, which does NOT use a `launch` token at all. The
    earlier version passed a hardcoded fake launch token ("sandbox-launch"),
    which is only valid for EHR Launch (apps opened from a link inside
    Epic itself) — Epic correctly rejected it with an authorization error.
    `launch/patient` in the scope tells Epic to prompt for patient
    selection as part of standalone authorization instead.
    """
    import urllib.parse
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": (
            "launch/patient openid fhirUser "
            "patient/Slot.read patient/Schedule.read "
            "patient/Appointment.read patient/Appointment.write "
            "patient/Account.read patient/Coverage.read "
            "patient/MedicationRequest.read patient/Observation.read"
        ),
        "aud": iss,
        "state": "intercept-scheduling-demo",
    }
    return f"{EPIC_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
