"""Second Opinion — review board configuration.

Three specialist agents collaborate in one Band room to review an incoming FDA
safety signal. The Regulatory officer runs intake; the Clinical Reviewer drafts
the assessment; the Safety Verifier independently challenges it; escalation goes
to the human reviewer who watches the room.
"""
from __future__ import annotations

import os

from board.case_data import build_incoming_signal

AGENTS = [
    {
        "config_key": "clinical_reviewer",
        "name": "Clinical Reviewer",
        "description": "Lead clinical assessor AI agent on a hospital drug-safety review board.",
    },
    {
        "config_key": "safety_verifier",
        "name": "Safety Verifier",
        "description": "Independent second-opinion AI agent that challenges the clinical assessment.",
    },
    {
        "config_key": "regulatory_compliance",
        "name": "Regulatory and Compliance Officer",
        "description": "Intake and final compliance-gate AI agent for FDA signal review.",
    },
]

AGENT_MODULES = [
    "board.clinical_reviewer",
    "board.safety_verifier",
    "board.regulatory_compliance",
]


def get_kickoff_config(agent_ids: dict, agent_names: dict) -> dict:
    """Return room topology and the intake message that opens the review.

    Set DSR_LIVE=0 to use the deterministic frozen case (recommended for a
    recorded demo); default pulls a live openFDA signal.
    """
    live = os.environ.get("DSR_LIVE", "1") != "0"
    signal = build_incoming_signal(live=live)
    clinical_name = agent_names["clinical_reviewer"]

    message = (
        f"{signal}\n\n"
        f"@{clinical_name}, this signal is assigned to you. Provide your patient-safety "
        "assessment and a draft recommendation with sources and confidence, then obtain "
        "an independent second opinion before anything is final."
    )

    return {
        "rooms": [
            {
                "name": "second-opinion",
                "owner": "regulatory_compliance",
                "participants": ["clinical_reviewer", "safety_verifier"],
                "message": message,
                "mentions": ["clinical_reviewer"],
            },
        ]
    }
