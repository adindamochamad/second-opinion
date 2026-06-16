"""Build the incoming FDA safety signal that opens a review.

The kickoff message is assembled here so the room always starts from a concrete,
*current* regulatory fact. We try openFDA live first; if the network or the API
is unavailable we fall back to a frozen, clearly-labelled illustrative case so a
live demo never dies on a flaky connection.

WHAT THE ROOM IS (AND IS NOT) TOLD
----------------------------------
A real intake desk knows its own formulary at a *coarse* grain: which drug is on
it and which clinics co-manage those patients. It does NOT pre-compute the
pharmacology. So the opening signal states the recall headline and the formulary
exposure WITHOUT naming the dangerous interaction or the specific co-prescribed
drug. Surfacing that interaction is the *work* — and it is exactly what a single
naive pass skips and the Safety Verifier is required to do.

This matters for honesty: the naive baseline and the board are handed the SAME
signal text (see naive_baseline.run_naive, which calls build_incoming_signal).
The only variable between them is the independent second opinion — not the data.

THE SCENARIOS
-------------
- clarithromycin_simvastatin (default): a low-looking Class II *superpotency*
  (out-of-specification HIGH assay) lot recall. The headline says nothing about
  interactions. The real hazard is that clarithromycin is a strong CYP3A4
  inhibitor that is FDA-contraindicated with simvastatin/lovastatin; blocked
  metabolism raises statin exposure -> myopathy / rhabdomyolysis in the
  co-prescribed statin cohort. Superpotent lots make the inhibitor *stronger*, so
  the directionality reinforces the hazard (more inhibitor -> more statin
  accumulation). Correct outcome: ESCALATE. The board must *discover* that the
  dangerous statins are the CYP3A4-dependent ones (simvastatin/lovastatin), not
  be handed it.

- benign_lot (control): a Class III label-typo recall of a topical with no
  meaningful interaction in the co-prescribed population. Correct outcome:
  ROUTINE / CONTINUE. This case exists to prove the board is NOT rigged to always
  escalate — an honest review board must also be able to stand down.

- qt_droperidol (generalization): a recall of an antiemetic carrying QT-
  prolongation risk in a peri-operative cohort already on other QT-prolonging
  agents. A second hidden-interaction hazard on a *different* axis (QT, not the
  CYP3A4/statin mechanism) to show the pattern is not flagship-specific. Correct
  outcome: ESCALATE.

Select with DSR_CASE (default clarithromycin_simvastatin). Set DSR_LIVE=0 for the
frozen text (recommended for a recorded demo); DSR_LIVE=1 pulls the live openFDA recall.
"""
from __future__ import annotations

import os

from tools.openfda import search_recalls


class Scenario:
    """One review case: the target drug, the formulary exposure the room knows,
    and a frozen fallback signal for offline/deterministic runs."""

    def __init__(
        self,
        key: str,
        drug: str,
        formulary_context: str,
        frozen_signal: str,
        expected: str,
    ) -> None:
        self.key = key
        self.drug = drug
        self.formulary_context = formulary_context
        self.frozen_signal = frozen_signal
        self.expected = expected  # the correct board outcome (for our own eval)


# ── clarithromycin / simvastatin — the flagship hidden-interaction case ───────
# Formulary context names the co-prescribed *statin cohort* but NOT the specific
# dangerous statin and NOT the interaction. The Verifier has to re-derive
# clarithromycin -> strong CYP3A4 inhibition -> blocked simvastatin/lovastatin
# metabolism -> myopathy/rhabdomyolysis (an FDA contraindication), then connect it
# to this cohort. Directionality is bulletproof: a SUPERpotent lot means MORE
# inhibitor, so the interaction is amplified, not reduced. That re-derivation is
# the catch a one-line "low-impact lot recall" triage never makes.
_CLARITHROMYCIN = Scenario(
    key="clarithromycin_simvastatin",
    drug="clarithromycin",
    formulary_context=(
        "Formulary context from our hospital system: clarithromycin is on the "
        "formulary for respiratory infections and H. pylori regimens. A large share "
        "of the patients receiving it are also on chronic statin therapy for "
        "cardiovascular prevention. (The intake desk has not assessed any drug-drug "
        "interaction; this was logged as a routine lot-quality recall.)"
    ),
    frozen_signal="""\
INCOMING FDA SAFETY SIGNAL (illustrative fallback case)

Drug: Clarithromycin 500 mg tablets
Source: FDA drug enforcement report (Class II)
Recall reason: Superpotent / out-of-specification HIGH assay results in 3
  distributed lots (active content above the labeled 500 mg).
Distribution: Multi-state, hospital and retail pharmacy.
Status: Ongoing.

Formulary context (from our hospital system):
- Clarithromycin is on the formulary for respiratory infections and H. pylori
  eradication.
- A large share of these patients are also on chronic statin therapy for
  cardiovascular prevention.
- The intake desk has not assessed any drug-drug interaction; this was logged
  as a routine lot-quality recall.
""",
    expected="ESCALATE-TO-HUMAN",
)

# ── benign control — correct answer is DO NOT escalate ────────────────────────
_BENIGN = Scenario(
    key="benign_lot",
    drug="hydrocortisone",
    formulary_context=(
        "Formulary context from our hospital system: hydrocortisone 1% topical "
        "cream is on the general formulary for dermatology and outpatient use. No "
        "systemic exposure of concern; no high-risk co-prescribed interaction "
        "population is associated with topical use."
    ),
    frozen_signal="""\
INCOMING FDA SAFETY SIGNAL (illustrative fallback case)

Drug: Hydrocortisone 1% topical cream, 30 g tubes
Source: FDA drug enforcement report (Class III)
Recall reason: Carton mislabeled with an incorrect expiration date; product
  itself is within specification and unaffected.
Distribution: Multi-state retail pharmacy.
Status: Ongoing.

Formulary context (from our hospital system):
- Hydrocortisone 1% topical cream is on the general formulary (dermatology /
  outpatient). Topical, minimal systemic absorption.
- No high-risk co-prescribed interaction population is associated with it.
""",
    expected="CONTINUE",
)

# ── QT generalization case — hidden hazard on a different axis ─────────────────
_QT = Scenario(
    key="qt_droperidol",
    drug="droperidol",
    formulary_context=(
        "Formulary context from our hospital system: droperidol is on the "
        "peri-operative / PACU formulary for post-operative nausea. Many of these "
        "patients receive other agents during their stay; the intake desk has not "
        "assessed cardiac-conduction risk for this recall."
    ),
    frozen_signal="""\
INCOMING FDA SAFETY SIGNAL (illustrative fallback case)

Drug: Droperidol injection, 2.5 mg/mL
Source: FDA drug enforcement report (Class II)
Recall reason: Particulate matter identified in 2 distributed lots.
Distribution: Hospital pharmacy, multi-state.
Status: Ongoing.

Formulary context (from our hospital system):
- Droperidol is on the peri-operative / PACU formulary for post-operative nausea.
- Many of these patients receive additional agents during their stay; the intake
  desk has not assessed cardiac-conduction (QT) risk for this recall.
""",
    expected="ESCALATE-TO-HUMAN",
)


SCENARIOS = {s.key: s for s in (_CLARITHROMYCIN, _BENIGN, _QT)}
DEFAULT_CASE = "clarithromycin_simvastatin"


def get_scenario(case: str | None = None) -> Scenario:
    """Resolve the active scenario from the argument or the DSR_CASE env var."""
    key = case or os.environ.get("DSR_CASE", DEFAULT_CASE)
    if key not in SCENARIOS:
        valid = ", ".join(SCENARIOS)
        raise SystemExit(f"Unknown DSR_CASE '{key}'. Valid cases: {valid}")
    return SCENARIOS[key]


def build_incoming_signal(case: str | None = None, live: bool = True) -> str:
    """Return the intake briefing text for the kickoff message.

    The SAME text is fed to the naive baseline, so the only difference between
    the naive pass and the board is the second opinion — never the input data.
    """
    scenario = get_scenario(case)

    if not live:
        return scenario.frozen_signal

    recalls = search_recalls(scenario.drug, limit=2)
    if not recalls:
        return scenario.frozen_signal

    lines = [
        "INCOMING FDA SAFETY SIGNAL (live openFDA)",
        "",
        f"Drug: {scenario.drug}",
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
    lines += ["", scenario.formulary_context]
    return "\n".join(lines)


if __name__ == "__main__":  # python -m board.case_data
    print(build_incoming_signal())
