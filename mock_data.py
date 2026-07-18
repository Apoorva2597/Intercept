"""
Demo scenarios for Abridge Intercept.

REBUILT to ground visit context in Abridge's real synthetic-ambient-fhir-25
dataset (25 real ambient-transcript encounters, provided for this
hackathon) instead of hand-authored synthetic text. Each scenario below
notes its real source record where one exists.

HONEST SCOPE OF WHAT'S REAL VS. AUTHORED:
- visit_date, signed_note, and transcript_excerpt are pulled directly from
  real dataset records (trimmed for length, not altered in substance).
- raw_message (the incoming patient portal message) is NOT in the dataset
  — Abridge's data is clinical encounters, not portal messages — so every
  patient message here is still authored, deliberately written to test a
  specific real documented detail from that record.
- Scenario 6 (scheduling) and Scenario 8 (multi-visit) remain fully
  synthetic: the dataset has no Slot/Schedule/Appointment resources, and
  each patient has exactly one encounter (no multi-visit history to
  select from). Both are labeled clearly below, not disguised as real.
- Scenarios 7 and 9 (billing) ground the VISIT metadata (date, type) in a
  real record, but no real charge/Account data exists in this dataset —
  the hard-rule refusal behavior is unaffected either way.

Source: synthetic-ambient-fhir-25, provided by Abridge. Everything in the
source dataset is itself synthetic (Synthea-generated patients, LLM-
generated transcripts/notes grounded in structured records) — not real
patient data at any point in this chain.
"""

CLINICAL_SCENARIOS = {

    # =================================================================
    # REAL DATA: synthetic-ambient-fhir-25, record 12
    # Patient: Julius Renner, visit 2025-07-13 — hypertension treatment
    # initiation + chronic low back pain + situational stress.
    # Real documented threshold (exact quote from the transcript):
    # "If the four a.m. math sessions become every night, or the mood
    # starts sinking, I want to hear about it early, not late."
    # Real current state (from the note): "early-morning waking with
    # rumination A FEW TIMES WEEKLY... preserved mood."
    # =================================================================
    "Scenario 1: Nurse Handles — Within Documented Threshold": {
        "patient_name": "Julius Renner",
        "submitted_folder": "Medical Question Pool",
        "data_source": "Abridge synthetic-ambient-fhir-25, record 12 (real visit context; message authored to test it)",
        "raw_message": (
            "Hi, following up after my visit last week. Still waking up a "
            "couple nights a week around 4am thinking about job stuff, but "
            "otherwise doing okay. Just wanted to mention it."
        ),
        "abridge_context": {
            "visit_date": "2025-07-13",
            "signed_note": (
                "Mr. Julius Renner is a 36-year-old man presenting for a general examination "
                "after a gap in care. He describes financial strain and early-morning waking "
                "with rumination a few times weekly, but preserved mood, appetite, energy, and "
                "day-to-day functioning; he identifies his state as situational stress, not an "
                "anxiety disorder (AUDIT-C and anxiety screen both minimal). Essential "
                "hypertension diagnosed previously but never treated after insurance change and "
                "job loss; home systolic readings reported in the 150s. Plan: start lisinopril "
                "10mg, amlodipine 2.5mg, and hydrochlorothiazide 25mg, all once daily each "
                "morning. Return in 4-6 weeks with home BP log to titrate. Return earlier if "
                "sleep disruption becomes nightly or mood declines."
            ),
            "transcript_excerpt": (
                "DR: The nurse went through the anxiety questionnaire with you — you scored a "
                "one, which is minimal, and that matches how you describe it. Stress, not an "
                "anxiety disorder. If the four a.m. math sessions become every night, or the "
                "mood starts sinking, I want to hear about it early, not late.\n"
                "PT: Fair enough."
            ),
        },
        "expected_routing_tag": "CLINICAL_ROUTING",
    },

    "Scenario 2: Provider Threshold Breached": {
        "patient_name": "Julius Renner",
        "submitted_folder": "Nursing Triage Pool",
        "data_source": "Abridge synthetic-ambient-fhir-25, record 12 (real visit context; message authored to test it)",
        "raw_message": (
            "Hi, it's Julius again. The 4am waking has been every single "
            "night this week, not just sometimes anymore, and I've noticed "
            "I just feel low most of the day now. Is this something I "
            "should be worried about?"
        ),
        "abridge_context": {
            "visit_date": "2025-07-13",
            "signed_note": (
                "Mr. Julius Renner is a 36-year-old man presenting for a general examination "
                "after a gap in care. He describes financial strain and early-morning waking "
                "with rumination a few times weekly, but preserved mood, appetite, energy, and "
                "day-to-day functioning; he identifies his state as situational stress, not an "
                "anxiety disorder (AUDIT-C and anxiety screen both minimal). Essential "
                "hypertension diagnosed previously but never treated after insurance change and "
                "job loss; home systolic readings reported in the 150s. Plan: start lisinopril "
                "10mg, amlodipine 2.5mg, and hydrochlorothiazide 25mg, all once daily each "
                "morning. Return in 4-6 weeks with home BP log to titrate. Return earlier if "
                "sleep disruption becomes nightly or mood declines."
            ),
            "transcript_excerpt": (
                "DR: The nurse went through the anxiety questionnaire with you — you scored a "
                "one, which is minimal, and that matches how you describe it. Stress, not an "
                "anxiety disorder. If the four a.m. math sessions become every night, or the "
                "mood starts sinking, I want to hear about it early, not late.\n"
                "PT: Fair enough."
            ),
        },
        "expected_routing_tag": "CLINICAL_ESCALATION",
    },

    "Scenario 3: Emergency Override — Hidden in Refill": {
        "patient_name": "Julius Renner",
        "submitted_folder": "Rx Refill Pool",
        "data_source": "Abridge synthetic-ambient-fhir-25, record 12 (real medication context; emergency content authored)",
        "raw_message": (
            "Can you refill my lisinopril? Also my left arm has been "
            "completely numb since this afternoon and it's spreading up to "
            "my jaw. Thanks."
        ),
        "abridge_context": {
            "visit_date": "2025-07-13",
            "signed_note": (
                "Essential hypertension diagnosed previously but never treated. Plan: start "
                "lisinopril 10mg, amlodipine 2.5mg, and hydrochlorothiazide 25mg, all once "
                "daily each morning, all low-dose generics."
            ),
            "transcript_excerpt": (
                "DR: Let's get you on a real regimen, all cheap generics — a few dollars a "
                "month each. Three medicines at low doses, working together: lisinopril ten "
                "milligrams once a day, amlodipine two and a half milligrams once a day, and "
                "hydrochlorothiazide twenty-five milligrams in the morning."
            ),
        },
        "expected_routing_tag": "CLINICAL_EMERGENCY",
    },

    "Scenario 4: Unconditional Symptom Trigger — Day 1, No Grace Period": {
        "patient_name": "Elias Wisozk",
        "submitted_folder": "Medical Question Pool",
        "data_source": "Abridge synthetic-ambient-fhir-25, record 6 (real visit context; message authored to test it)",
        "raw_message": (
            "Hi, I only started the hydrochlorothiazide yesterday. Since "
            "this morning I've been really lightheaded every time I stand "
            "up and I'm getting bad muscle cramps in my legs. Is this just "
            "part of adjusting to it?"
        ),
        "abridge_context": {
            "visit_date": "2026-06-18",
            "signed_note": (
                "Plan: start hydrochlorothiazide 25mg orally each morning for new essential "
                "hypertension and metabolic syndrome. Counseled on orthostatic symptoms and "
                "cramping — instructed to call if these occur, given risk of electrolyte "
                "shifts. Recheck BP in approximately 1 month with interval electrolytes."
            ),
            "transcript_excerpt": (
                "DR: If you feel lightheaded when you stand, or you get muscle cramps, call "
                "us — it can nudge your potassium and sodium, so we'll recheck bloodwork "
                "after you've been on it a while.\n"
                "PT: Fine. One pill, mornings."
            ),
        },
        "expected_routing_tag": "CLINICAL_EMERGENCY",
    },

    "Scenario 5: Refill — Routine, No Red Flags": {
        "patient_name": "Elias Wisozk",
        "submitted_folder": "Rx Refill Pool",
        "data_source": "Abridge synthetic-ambient-fhir-25, record 6 (real visit context; message authored to test it)",
        "raw_message": (
            "Hi, could I get a refill on my hydrochlorothiazide please? "
            "Doing fine on it, no issues, just need more before I run out."
        ),
        "abridge_context": {
            "visit_date": "2026-06-18",
            "signed_note": (
                "Plan: start hydrochlorothiazide 25mg orally each morning for new essential "
                "hypertension and metabolic syndrome. Counseled on orthostatic symptoms and "
                "cramping. Recheck BP in approximately 1 month with interval electrolytes."
            ),
            "transcript_excerpt": (
                "DR: Take it with breakfast. If you feel lightheaded when you stand, or you "
                "get muscle cramps, call us.\n"
                "PT: Fine. One pill, mornings. I can do that."
            ),
        },
        "expected_routing_tag": "REFILL_ROUTING",
    },

    "Scenario 7: Billing — Specific Charge Lookup (Hard Rule Test)": {
        "patient_name": "Julius Renner",
        "submitted_folder": "Billing Pool",
        "data_source": "Abridge synthetic-ambient-fhir-25, record 12 (real visit date/type; no real charge data exists in this dataset)",
        "raw_message": (
            "Hi, I need to know the exact amount I was charged for my "
            "visit last month so I can submit it to my HSA before the "
            "deadline. Can you give me the total?"
        ),
        "abridge_context": {
            "visit_date": "2025-07-13",
            "signed_note": "General examination visit — hypertension treatment initiation, chronic low back pain, psychosocial screening.",
            "transcript_excerpt": "",
        },
        "expected_routing_tag": "BILLING_ROUTING",
    },

    # =================================================================
    # HYBRID: Eva Casas is a REAL patient (record 8) — her 2016 annual
    # exam is used as-is. The dataset has no multi-visit patients, so ONE
    # additional visit is authored here, deliberately consistent with her
    # REAL documented pattern ("winter-predominant" knee flares, managed
    # with OTC naproxen) rather than inventing an unrelated detail. This
    # is the minimal possible invention needed to test the context-
    # selector mechanism, not a fully fictional patient.
    # =================================================================
    "Scenario 8: Multi-Visit Context — Issue From 2 Visits Ago": {
        "patient_name": "Eva Casas",
        "submitted_folder": "Medical Question Pool",
        "data_source": "HYBRID — real patient (Abridge record 8), one real visit + one authored visit consistent with her real documented knee pattern; the dataset has no multi-visit patients to draw a fully real case from",
        "raw_message": (
            "Hi, the knee flare-up we talked about back in the winter still "
            "hasn't really settled down. It's been a lot longer than you "
            "said to expect. Should I come in?"
        ),
        "abridge_context": [
            {
                "visit_date": "2016-02-14",
                "signed_note": (
                    "[Authored, consistent with patient's real documented winter-predominant "
                    "knee osteoarthritis pattern] Eva Casas presents with a winter flare of known "
                    "right knee osteoarthritis, pain 5/10, no swelling or instability. Advised "
                    "continued OTC naproxen with food and activity modification. Follow up if "
                    "pain persists beyond 3 weeks despite conservative management."
                ),
                "transcript_excerpt": "Provider: 'Give it three weeks on the naproxen — if it's still bothering you after that, come back in.'",
            },
            {
                "visit_date": "2016-08-30",
                "signed_note": (
                    "[Real — Abridge record 8] Eva Casas is a 62-year-old woman presenting for "
                    "her annual general examination. Her known osteoarthritis of the knee is "
                    "currently asymptomatic (pain 0/10 today); winter-predominant pattern "
                    "managed with OTC naproxen as needed with food, without dyspepsia or knee "
                    "swelling."
                ),
                "transcript_excerpt": "DR: Your inheritance is behaving. [visit focused on A1c, lipids, and general wellness — knee not a current concern]",
            },
            {
                "visit_date": "2017-01-10",
                "signed_note": (
                    "[Authored, unrelated] Follow-up for hyperlipidemia management. Lipid panel "
                    "stable on current diet and activity plan. No other concerns raised at this "
                    "visit."
                ),
                "transcript_excerpt": "",
            },
        ],
        "expected_routing_tag": "CLINICAL_ESCALATION",
    },

    # =================================================================
    # REAL DATA: synthetic-ambient-fhir-25, record 12 (Julius Renner)
    # Demonstrates the scheduling specialist's core capability: the
    # message alone doesn't give enough to schedule correctly ("due
    # soon," no date) — the documented visit history already answers it.
    # =================================================================
    "Scenario 12: Scheduling — Ambiguous Request Resolved by Context": {
        "patient_name": "Julius Renner",
        "submitted_folder": "Scheduling Pool",
        "data_source": "Abridge synthetic-ambient-fhir-25, record 12 (real visit date used to resolve an intentionally underspecified message)",
        "raw_message": (
            "Hi, I think I'm about due for my annual physical again. Can "
            "you get me scheduled sometime soon?"
        ),
        "abridge_context": {
            "visit_date": "2025-07-13",
            "signed_note": (
                "General examination visit — annual physical. Hypertension treatment "
                "initiation, chronic low back pain, psychosocial screening."
            ),
            "transcript_excerpt": "",
        },
        "expected_routing_tag": "SCHEDULING_ROUTING",
    },

    # =================================================================
    # REAL DATA + ONE MINIMAL AUTHORED VISIT: Julius Renner's real July
    # 13 visit documents a gingivitis finding AND a real placed dental
    # referral. One authored follow-through visit on that real referral
    # gives him two genuine candidate visits — this is real
    # discrimination between documents, not reading the only one
    # available (the earlier version of this scenario had that problem).
    # =================================================================
    "Scenario 13: Billing — Ambiguous Charge Resolved by Context": {
        "patient_name": "Julius Renner",
        "submitted_folder": "Billing Pool",
        "data_source": "HYBRID — real July visit (including its real dental-referral finding) plus one authored follow-through visit; message requires genuinely distinguishing between the two, not just reading a single available document",
        "raw_message": (
            "Hey, I got a charge for something dental-related recently and "
            "I'm not totally sure what treatment it was for. Can you check?"
        ),
        "abridge_context": [
            {
                "visit_date": "2025-07-13",
                "signed_note": (
                    "General examination — hypertension treatment initiation, chronic low back "
                    "pain, psychosocial screening. Mandibular gingiva erythematous and edematous "
                    "with bleeding on light contact. Patient referral for dental care placed for "
                    "cleaning and examination."
                ),
                "transcript_excerpt": "DR: I am having them print a dental referral for you before you leave.",
            },
            {
                "visit_date": "2025-08-04",
                "signed_note": (
                    "[Authored, consistent with the real dental referral placed 2025-07-13] "
                    "Dental cleaning and periodontal examination visit, following the general "
                    "medicine referral for gingivitis. Scaling and root planing performed."
                ),
                "transcript_excerpt": "",
            },
        ],
        "expected_routing_tag": "BILLING_ROUTING",
    },

    # =================================================================
    # REAL DATA + AUTHORED CONTEXT, reusing Eva Casas's same multi-visit
    # structure as Scenario 8: one real visit with actual documented lab
    # VALUES, two authored visits that mention labs only in passing
    # without new values. Genuine discrimination required — not just
    # reading the one document available.
    # =================================================================
    "Scenario 14: Results — Ambiguous Request Resolved by Context": {
        "patient_name": "Eva Casas",
        "submitted_folder": "Results Pool",
        "data_source": "HYBRID — real visit with real lab values (Abridge record 8) plus two authored visits without new values; message requires identifying which visit actually has retrievable results, not reading a single available document",
        "raw_message": "Hi, can you send me my cholesterol results from my last check? Just want them for my records.",
        "abridge_context": [
            {
                "visit_date": "2016-02-14",
                "signed_note": (
                    "[Authored, consistent with patient's real documented knee pattern] Winter "
                    "flare of known right knee osteoarthritis. No labs drawn at this visit."
                ),
                "transcript_excerpt": "",
            },
            {
                "visit_date": "2016-08-30",
                "signed_note": (
                    "[Real — Abridge record 8] Annual exam labs: Hemoglobin A1c 6.11%, glucose "
                    "79 mg/dL, total cholesterol 168 mg/dL, LDL 87 mg/dL, HDL 59 mg/dL, "
                    "triglycerides 110 mg/dL. Result released to patient portal following "
                    "provider review."
                ),
                "transcript_excerpt": (
                    "DR: Your inheritance is behaving. Total cholesterol 168, the bad LDL is "
                    "87, the good HDL is 59."
                ),
            },
            {
                "visit_date": "2017-01-10",
                "signed_note": (
                    "[Authored, unrelated] Follow-up for hyperlipidemia management. Diet and "
                    "activity plan reviewed; no new lipid panel drawn at this visit, prior "
                    "results remain the most recent on file."
                ),
                "transcript_excerpt": "",
            },
        ],
        "expected_routing_tag": "RESULTS_ROUTING",
    },

    "Scenario 10: Results — Retrieve Released, Routine Value": {
        "patient_name": "Eva Casas",
        "submitted_folder": "Results Pool",
        "data_source": "Abridge synthetic-ambient-fhir-25, record 8 (real lab value and real documented plan)",
        "raw_message": "Hi, can you send me my A1c result from my last visit? Just want it for my records.",
        "abridge_context": {
            "visit_date": "2016-08-30",
            "signed_note": (
                "Hemoglobin A1c 6.11%, holding steady in the prediabetes range, same as prior "
                "year. Plan: continue dietary portion control and daily walking, repeat A1c at "
                "next annual visit — no medication or interim recheck indicated. Result "
                "released to patient portal following provider review."
            ),
            "transcript_excerpt": (
                "DR: Your sugar average — the A1c — is 6.11 percent. Still prediabetes, same "
                "neighborhood as last year, holding steady."
            ),
        },
        "expected_routing_tag": "RESULTS_ROUTING",
    },

    "Scenario 11: Results — Interpretation Requested (Must Refuse)": {
        "patient_name": "Eva Casas",
        "submitted_folder": "Results Pool",
        "data_source": "Abridge synthetic-ambient-fhir-25, record 8 (real lab value; interpretation is always refused regardless)",
        "raw_message": "Why is my A1c elevated? Should I be worried it's going to turn into diabetes?",
        "abridge_context": {
            "visit_date": "2016-08-30",
            "signed_note": (
                "Hemoglobin A1c 6.11%, holding steady in the prediabetes range, same as prior "
                "year. Plan: continue dietary portion control and daily walking, repeat A1c at "
                "next annual visit."
            ),
            "transcript_excerpt": (
                "DR: Your sugar average — the A1c — is 6.11 percent. Still prediabetes, same "
                "neighborhood as last year, holding steady."
            ),
        },
        "expected_routing_tag": "RESULTS_ESCALATION",
    },
}
