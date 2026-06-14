"""Build the incoming FDA safety signal that opens a review.

The kickoff message is assembled here so the room always starts from a concrete,
*current* regulatory fact. We try openFDA live first; if the network or the API
is unavailable we fall back to a frozen, clearly-labelled illustrative case so a
live demo never dies on a flaky connection (the same pattern MediGuard uses with
DEMO_FALLBACK).

THE ENGINEERED TRAP (why a single naive agent gets this wrong)
-------------------------------------------------------------
The signal *headline* is a limited-lot Class II recall of amiodarone — looks
low-impact, "continue, monitor lots." A naive single agent anchored on the
recall headline recommends "continue, low population risk."

The real hazard is NOT the lot recall. Amiodarone is a potent CYP2C9/3A4
inhibitor that raises INR in patients co-prescribed warfarin — a large slice of
a cardiology formulary — driving major-bleed risk. The correct safety action is
INR re-checks for co-prescribed warfarin patients, which the headline never
mentions. The Safety Verifier is the agent that catches this by pulling the live
label interaction section + PubMed, and escalates.
"""
from __future__ import annotations

from tools.openfda import search_recalls

TARGET_DRUG = "amiodarone"
CO_PRESCRIBED = "warfarin"

# Frozen, illustrative fallback (used only when openFDA is unreachable). Phrased
# the way a real intake desk would log it.
FROZEN_SIGNAL = """\
INCOMING FDA SAFETY SIGNAL (illustrative fallback case)

Drug: Amiodarone HCl 200 mg tablets
Source: FDA drug enforcement report (Class II)
Recall reason: Subpotent / out-of-specification results in 3 distributed lots.
Distribution: Multi-state, hospital and retail pharmacy.
Status: Ongoing.

Formulary context (from our hospital system):
- Amiodarone is on the cardiology formulary.
- 410 currently active patients are co-prescribed warfarin.
"""

FORMULARY_CONTEXT = (
    "Formulary context from our hospital system: amiodarone is on the cardiology "
    f"formulary, and 410 currently active patients are co-prescribed {CO_PRESCRIBED}."
)


def build_incoming_signal(drug: str = TARGET_DRUG, live: bool = True) -> str:
    """Return the intake briefing text for the kickoff message."""
    if not live:
        return FROZEN_SIGNAL

    recalls = search_recalls(drug, limit=2)
    if not recalls:
        return FROZEN_SIGNAL

    lines = [
        "INCOMING FDA SAFETY SIGNAL (live openFDA)",
        "",
        f"Drug: {drug}",
        "Source: FDA drug enforcement report (api.fda.gov/drug/enforcement)",
        "",
        "Most recent enforcement report(s):",
    ]
    for r in recalls:
        lines.append(
            f"- {r['classification']} | status {r['status']} | initiated {r['recall_date']}"
        )
        if r["reason"]:
            lines.append(f"  Reason: {r['reason'][:240]}")
    lines += ["", FORMULARY_CONTEXT]
    return "\n".join(lines)


if __name__ == "__main__":  # python -m scenarios.drug_safety_review.case_data
    print(build_incoming_signal())
