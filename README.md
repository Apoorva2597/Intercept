# Abridge Intercept

An EMR inbox management system that routes patient portal messages using
documented clinical context — not just the message content. The same
reported symptom can mean two different things depending on what a
provider already documented and signed.

Built for the Abridge × Anthropic × Lightspeed Hackathon, July 18, 2026.

## The core idea

Most inbox triage reads only what the patient wrote. Intercept also reads
what the provider already said — a documented threshold, a stated
follow-up window, an explicit "call us if X happens" — and routes
accordingly. The same message can produce different, correct outcomes
depending on that context.

## What it covers

- **Clinical / Refill** — compares reported symptoms against documented
  thresholds
- **Scheduling** — resolves ambiguous requests ("I'm due soon," no date
  given) using real visit history; real Epic sandbox integration for
  actual slot availability and booking (human-confirmation gated)
- **Billing** — confirms *what* a charge concerns from context; never
  invents a dollar figure, structurally enforced
- **Results** — retrieves released values; never interprets what a value
  means, structurally enforced

## Architecture

- Fast safety-screening orchestrator (Haiku) runs on every message first
- Domain specialists (Sonnet) reason over message + real documented
  context
- Defense-in-depth: each specialist independently re-checks for missed
  emergencies
- Structural safety via Pydantic validators — certain dangerous outputs
  are impossible to represent, not just discouraged
- Multi-visit context selection for patients with more than one
  documented visit
- Real EHR integration: SMART on FHIR (standalone launch) + SMART
  Backend Services (JWT-based, no interactive login) against Epic's
  sandbox

## Data

Visit context is grounded in Abridge's provided synthetic-ambient-fhir-25
dataset (real transcripts/notes from synthetic patients) where available;
scenarios are labeled with their actual data source, real or authored.

## Running it

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn main:app --reload
```

Open `http://127.0.0.1:8000`.

## Honest limitations

- Evaluation labels are self-authored, not clinician-reviewed
- Repeated-contact thresholds are configurable starting points, not
  validated clinical constants
- No real pharmacy write-back exists; refill "actions" are audit-log
  entries, not real orders
- Scheduling scenario 6 area is fully synthetic — no real post-op
  scheduling data existed in the provided dataset

## License

MIT
