"""
FastAPI backend for Abridge Intercept.

Replaces the Streamlit UI with a plain REST API + static frontend, since
Streamlit is not permitted for this event. Wraps InterceptEngine and
InboxManager exactly as they are — no logic duplicated here, this is a
thin HTTP layer over the same engine used in every test in this repo.
"""

import os
from datetime import datetime
from typing import Optional, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from pipeline import InterceptEngine
from inbox_manager import InboxManager
from mock_data import CLINICAL_SCENARIOS
from adversarial_cases import ADVERSARIAL_CASES
from epic_scheduling import exchange_code_for_token, EpicSchedulingClient, build_authorization_url, get_backend_access_token

app = FastAPI(title="Abridge Intercept API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def resolve_api_key(request_key: Optional[str]) -> str:
    """Server-side ANTHROPIC_API_KEY takes priority over anything sent from
    the frontend — this is what lets the key field disappear from the UI
    entirely: set it once via environment variable when starting the
    server, and no session ever needs to paste it in or display it on
    screen during a demo. request_key is kept as a fallback only, for
    local dev flexibility if no environment variable is set."""
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        return env_key
    if request_key:
        return request_key
    raise HTTPException(
        status_code=400,
        detail="No API key available — set ANTHROPIC_API_KEY as an environment variable before starting the server.",
    )


def _serialize(output) -> dict:
    """Pydantic output -> plain dict, safe for JSON, including fields that
    only exist on some output types (scheduling/billing-specific fields)."""
    return output.model_dump()


def _serialize_diag(diag: dict) -> dict:
    """diagnostics contains Pydantic objects (ContextSelection, specialist
    outputs inside secondary_resolutions) that need explicit serialization —
    a plain dict isn't JSON-safe as-is."""
    out = dict(diag)
    ctx = out.get("context_selection")
    if ctx:
        out["context_selection"] = {
            **ctx,
            "selection_output": ctx["selection_output"].model_dump() if ctx.get("selection_output") else None,
        }
    secs = out.get("secondary_resolutions")
    if secs:
        out["secondary_resolutions"] = [
            {**s, "output": s["output"].model_dump()} if "output" in s else s
            for s in secs
        ]
    return out


class RouteRequest(BaseModel):
    api_key: Optional[str] = None
    message_text: str
    abridge_note: Optional[Any] = None


class InboxQueueRequest(BaseModel):
    api_key: Optional[str] = None


class ApiKeyRequest(BaseModel):
    api_key: Optional[str] = None


@app.get("/api/scenarios")
def list_scenarios():
    return {
        name: {
            "patient_name": s["patient_name"],
            "submitted_folder": s["submitted_folder"],
            "raw_message": s["raw_message"],
            "abridge_context": s["abridge_context"],
            "expected_routing_tag": s["expected_routing_tag"],
            "data_source": s.get("data_source", "Synthetic"),
        }
        for name, s in CLINICAL_SCENARIOS.items()
    }


@app.post("/api/route")
async def route_message(req: RouteRequest):
    try:
        engine = InterceptEngine(api_key=resolve_api_key(req.api_key))
        specialist, output, diag = await engine.process_message(req.message_text, req.abridge_note)
        return {
            "specialist": specialist,
            "output": _serialize(output),
            "diagnostics": _serialize_diag(diag),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/route-comparison")
async def route_comparison(req: RouteRequest):
    """WITHOUT vs WITH context, for Scenario 2's live thesis toggle."""
    try:
        engine = InterceptEngine(api_key=resolve_api_key(req.api_key))
        spec_no, out_no, diag_no = await engine.process_message(req.message_text, None)
        spec_yes, out_yes, diag_yes = await engine.process_message(req.message_text, req.abridge_note)
        return {
            "without_context": {"specialist": spec_no, "output": _serialize(out_no), "diagnostics": _serialize_diag(diag_no)},
            "with_context": {"specialist": spec_yes, "output": _serialize(out_yes), "diagnostics": _serialize_diag(diag_yes)},
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/evaluate")
async def evaluate(req: ApiKeyRequest):
    try:
        engine = InterceptEngine(api_key=resolve_api_key(req.api_key))
        results = await engine.evaluate_scenarios(CLINICAL_SCENARIOS)
        agreed = sum(r["agreed"] for r in results)
        return {"results": results, "agreement_rate": agreed / len(results) if results else 0, "total": len(results), "agreed": agreed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/adversarial")
async def adversarial(req: ApiKeyRequest):
    try:
        engine = InterceptEngine(api_key=resolve_api_key(req.api_key))
        results = await engine.run_adversarial_suite(ADVERSARIAL_CASES)
        passed = sum(r["passed"] for r in results)
        serializable = []
        for r in results:
            r2 = dict(r)
            serializable.append(r2)
        return {"results": serializable, "passed": passed, "total": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/dashboard")
async def dashboard(req: ApiKeyRequest):
    try:
        engine = InterceptEngine(api_key=resolve_api_key(req.api_key))
        stats = await engine.inbox_dashboard_stats(CLINICAL_SCENARIOS)
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/inbox-queue")
async def inbox_queue(req: InboxQueueRequest):
    try:
        engine = InterceptEngine(api_key=resolve_api_key(req.api_key))
        inbox = InboxManager(engine, db_path="intercept_inbox_history.db")

        demo_batch = [
            {"patient_id": "patient_001", "message_text": CLINICAL_SCENARIOS["Scenario 1: Nurse Handles — Within Documented Threshold"]["raw_message"], "abridge_note": CLINICAL_SCENARIOS["Scenario 1: Nurse Handles — Within Documented Threshold"]["abridge_context"]},
            {"patient_id": "patient_002", "message_text": CLINICAL_SCENARIOS["Scenario 3: Emergency Override — Hidden in Refill"]["raw_message"], "abridge_note": CLINICAL_SCENARIOS["Scenario 3: Emergency Override — Hidden in Refill"]["abridge_context"]},
            {"patient_id": "patient_003", "message_text": CLINICAL_SCENARIOS["Scenario 12: Scheduling — Ambiguous Request Resolved by Context"]["raw_message"], "abridge_note": CLINICAL_SCENARIOS["Scenario 12: Scheduling — Ambiguous Request Resolved by Context"]["abridge_context"]},
            {"patient_id": "patient_004", "message_text": "Hi, I texted a few days ago about this same headache and it's still not going away.",
             "abridge_note": {"visit_date": "2026-07-01", "signed_note": "Discussed tension headaches, advised OTC pain relief and hydration, follow up if not improved.", "transcript_excerpt": ""}},
            {"patient_id": "patient_004", "message_text": "Following up again — the headache is still there, third time reaching out about this.",
             "abridge_note": {"visit_date": "2026-07-01", "signed_note": "Discussed tension headaches, advised OTC pain relief and hydration, follow up if not improved.", "transcript_excerpt": ""}},
            {"patient_id": "patient_005", "message_text": CLINICAL_SCENARIOS["Scenario 5: Refill — Routine, No Red Flags"]["raw_message"], "abridge_note": CLINICAL_SCENARIOS["Scenario 5: Refill — Routine, No Red Flags"]["abridge_context"]},
        ]

        ranked = await inbox.prioritize_queue(demo_batch)
        return {
            "ranked": [
                {
                    "patient_id": r["patient_id"],
                    "message_text": r["message_text"],
                    "specialist": r["specialist"],
                    "output": _serialize(r["output"]),
                    "diagnostics": _serialize_diag(r["diagnostics"]),
                    "priority_score": r["priority_score"],
                }
                for r in ranked
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# EPIC OAUTH FLOW — completes the manual step described in epic_scheduling.py.
#
# HONEST STATUS: these two endpoints are real, standard OAuth2 code and have
# never been exercised end-to-end from within this environment (network
# egress here blocks fhir.epic.com — confirmed via a direct test, see
# epic_scheduling.py's module docstring). They are ready to test the moment
# a real client_id exists, on a machine with real internet access.
#
# Setup required (one-time, ~1 hour to sync after registering):
#   1. Register a free app at https://open.epic.com/MyApps
#      - Mark it ready for the Sandbox testing environment
#      - Request scopes: launch openid fhirUser patient/Slot.read patient/Appointment.read
#      - Redirect URI: http://127.0.0.1:8000/api/epic/callback (or your host:port)
#   2. Wait ~1 hour for sandbox sync, then visit /api/epic/start below.
# ---------------------------------------------------------------------------

# In-memory token store for the demo — a real deployment would use a proper
# session/token store, not a module-level dict. Fine for a single-user demo.
_epic_session: dict = {"access_token": None, "patient_fhir_id": None}


@app.get("/api/epic/start")
def epic_start(client_id: str, redirect_uri: str = "http://127.0.0.1:8000/api/epic/callback"):
    """Step 1: redirect the browser to Epic's real consent page. This is the
    one step that cannot be automated — Epic requires a real interactive
    login. Visit this URL in a browser (not curl) with your own client_id
    once your sandbox app has synced.

    FIXED (found live during testing): the earlier version passed a fake
    hardcoded 'launch' token, which is only valid for EHR Launch — Epic
    correctly rejected it with 'something went wrong to authorize the
    client'. This app is standalone, so Standalone Launch is correct:
    no launch token, launch/patient scope instead."""
    from fastapi.responses import RedirectResponse
    auth_url = build_authorization_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        iss="https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/STU3",
    )
    return RedirectResponse(auth_url)


@app.get("/api/epic/callback")
def epic_callback(code: str, client_id: str, redirect_uri: str = "http://127.0.0.1:8000/api/epic/callback"):
    """Step 2: Epic redirects here after consent, with a real authorization
    code. Trade it for a real access token + patient context."""
    try:
        token_response = exchange_code_for_token(client_id, redirect_uri, code)
        _epic_session["access_token"] = token_response.get("access_token")
        _epic_session["patient_fhir_id"] = token_response.get("patient")
        return {
            "status": "connected",
            "patient_fhir_id": token_response.get("patient"),
            "note": "Token stored for this session. Try /api/epic/slots next.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Token exchange failed: {e}")


@app.get("/api/epic/connect-backend")
def epic_connect_backend(client_id: str):
    """Alternate connection path — SMART Backend Services (JWT client
    assertion), no interactive login at all. Built as a direct fix for
    standalone launch's login-wall problem: no test-patient credential
    needed, just the cryptographic keypair already registered with Epic.
    Slot.Read doesn't require a specific patient context, so this is a
    clean fit for the scheduling-availability use case specifically —
    patient-scoped resources (billing, labs, medications) still need the
    standalone launch flow above, since Backend Services has no patient
    in context at all."""
    try:
        token = get_backend_access_token(client_id, "epic_private_key.pem")
        _epic_session["access_token"] = token
        _epic_session["patient_fhir_id"] = None  # backend services has no patient context
        return {"status": "connected", "auth_method": "backend_services", "note": "No interactive login required. Try /api/epic/slots next."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backend Services auth failed: {e}")


@app.get("/api/epic/slots")
def epic_slots():
    """Once connected, query real available Slot resources from the sandbox."""
    if not _epic_session["access_token"]:
        raise HTTPException(status_code=400, detail="Not connected — visit /api/epic/start first.")
    client = EpicSchedulingClient(
        access_token=_epic_session["access_token"],
        patient_fhir_id=_epic_session["patient_fhir_id"],
    )
    try:
        slots = client.find_available_slots()
        return {"slots": slots, "count": len(slots)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Slot query failed: {e}")
    finally:
        client.close()


@app.get("/api/epic/billing")
def epic_billing():
    """Real Account (Premium Billing) lookup — read-only, same risk tier
    as slot lookup. Returns null balance if nothing found; never guesses."""
    if not _epic_session["access_token"]:
        raise HTTPException(status_code=400, detail="Not connected — visit /api/epic/start first.")
    client = EpicSchedulingClient(
        access_token=_epic_session["access_token"],
        patient_fhir_id=_epic_session["patient_fhir_id"],
    )
    try:
        billing = client.get_account_billing()
        coverage = client.get_coverage()
        return {"billing": billing, "coverage": coverage}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Billing query failed: {e}")
    finally:
        client.close()


@app.get("/api/epic/labs")
def epic_labs():
    """Real Observation (lab) lookup — read-only. Returns real results
    with their real status; does NOT assert a patient-release flag that
    may not exist on this resource — the results specialist's own hard
    rule still governs what's safe to actually show a patient."""
    if not _epic_session["access_token"]:
        raise HTTPException(status_code=400, detail="Not connected — visit /api/epic/start first.")
    client = EpicSchedulingClient(
        access_token=_epic_session["access_token"],
        patient_fhir_id=_epic_session["patient_fhir_id"],
    )
    try:
        results = client.get_lab_results()
        return {"results": results, "count": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lab results query failed: {e}")
    finally:
        client.close()


class BookingProposal(BaseModel):
    slot_id: str


class BookingConfirmation(BaseModel):
    slot_id: str
    confirmed: bool  # the human-gate — must be explicitly true, no default


@app.post("/api/epic/propose-booking")
def epic_propose_booking(req: BookingProposal):
    """Step 1 of 2. Never executes a write — see propose_booking()'s
    docstring for why this split exists."""
    client = EpicSchedulingClient(access_token=_epic_session["access_token"] or "unused-for-proposal")
    return client.propose_booking(req.slot_id)


@app.post("/api/epic/confirm-booking")
def epic_confirm_booking(req: BookingConfirmation):
    """Step 2 of 2 — the ONLY endpoint that calls Epic's real $book. Requires
    an explicit confirmed=true from a genuine user action (never called
    automatically by the reasoning pipeline). This is the human-in-the-loop
    gate: a specialist can recommend a slot, but only a person confirming
    through this endpoint actually books it."""
    if not req.confirmed:
        raise HTTPException(status_code=400, detail="Booking requires explicit human confirmation (confirmed=true).")
    if not _epic_session["access_token"] or not _epic_session["patient_fhir_id"]:
        raise HTTPException(status_code=400, detail="Not connected — visit /api/epic/start first.")
    client = EpicSchedulingClient(
        access_token=_epic_session["access_token"],
        patient_fhir_id=_epic_session["patient_fhir_id"],
    )
    try:
        result = client.confirm_booking(req.slot_id, _epic_session["patient_fhir_id"])
        return {"status": "booked", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Booking failed: {e}")
    finally:
        client.close()


# Static mount MUST be registered last — Starlette matches routes in
# registration order, and a mount at "/" would otherwise intercept every
# /api/epic/* request as a static-file lookup before it ever reached the
# routes above. (Caught this exact bug during testing — see below.)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
