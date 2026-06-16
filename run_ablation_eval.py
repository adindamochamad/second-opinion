"""3-arm ablation harness -> CSV.

Arm A: equalized naive single agent (neutral instruction, matched budget).
Arm B: single agent + the board's mandatory re-derivation CoT (isolates CoT).
Arm C: full multi-agent board via orchestrator.run_review (opt-in: --with-board;
       only runs for scenarios present in board.case_data; needs run_all.py up).

CSV columns: Scenario_ID, Stratum, Expected, Arm, Repeat, Predicted,
             Hazard_Detected, False_Escalation, Tokens_Consumed, Token_Method,
             Execution_Time_s

Usage:
    python run_ablation_eval.py                 # arms A + B, all scenarios
    python run_ablation_eval.py --limit 4       # first 4 scenarios (cheap smoke test)
    python run_ablation_eval.py --with-board    # also runs arm C (needs run_all.py up)
    REPEATS=3 python run_ablation_eval.py
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import re
import time

import anthropic
from dotenv import load_dotenv

from naive_baseline import INSTRUCTION, NAIVE_MAX_TOKENS

# ── Pre-registered, stratified scenarios (label = correct outcome) ───────────
# 4 hidden-hazard, 3 explicit-hazard, 3 benign-distractor.
SCENARIOS = [
    ("hidden_amiodarone_warfarin", "hidden_hazard", "ESCALATE",
     "Amiodarone 200mg tablets, Class II recall for subpotent/out-of-spec lots. Formulary: "
     "amiodarone is on the cardiology formulary; a large cohort is co-managed by the "
     "anticoagulation clinic on chronic oral anticoagulation. Intake logged it as routine lot-quality."),
    ("hidden_droperidol_qt", "hidden_hazard", "ESCALATE",
     "Droperidol injection 2.5mg/mL, Class II recall for particulate matter. Formulary: PACU "
     "post-op nausea; many patients receive other agents during their stay; cardiac-conduction "
     "risk not assessed."),
    ("hidden_clarithromycin_statin", "hidden_hazard", "ESCALATE",
     "Clarithromycin 500mg tablets, Class II recall for dissolution failure. Formulary: used in "
     "inpatient respiratory; a large share of these patients are on simvastatin. Interaction not assessed."),
    ("hidden_fluconazole_warfarin", "hidden_hazard", "ESCALATE",
     "Fluconazole 150mg, Class III recall for a bottle-count error. Formulary: outpatient antifungal; "
     "co-prescribed frequently with warfarin in the anticoag clinic. Logged as packaging-only."),
    ("explicit_methotrexate_dosing", "explicit_hazard", "ESCALATE",
     "Methotrexate 2.5mg, Class II recall; FDA report notes dosing-frequency labeling confusion "
     "associated with serious toxicity. Formulary: rheumatology and oncology, weekly dosing."),
    ("explicit_heparin_contamination", "explicit_hazard", "ESCALATE",
     "Heparin 1000u/mL, Class I recall for potential microbial contamination of distributed lots. "
     "Formulary: ICU continuous infusion."),
    ("explicit_levothyroxine_potency", "explicit_hazard", "ESCALATE",
     "Levothyroxine 100mcg, Class II recall for sub-/super-potency across lots. Formulary: large "
     "outpatient population; narrow therapeutic index."),
    ("benign_carton_typo", "benign", "CONTINUE",
     "Hydrocortisone 1% topical cream, Class III recall: carton mislabeled with an incorrect "
     "expiration date; product within spec. Formulary: dermatology/outpatient topical; minimal "
     "systemic absorption; no high-risk co-prescribed population."),
    ("benign_packaging_color", "benign", "CONTINUE",
     "Artificial tears, Class III recall for a secondary-carton color mismatch; product unaffected. "
     "Formulary: OTC ophthalmic comfort use; no systemic exposure."),
    ("benign_count_variance", "benign", "CONTINUE",
     "Multivitamin tablets, Class III recall for a bottle fill-count variance. Formulary: general "
     "wellness; no narrow-TI drug, no high-risk interaction population."),
]

# Eval id -> DSR_CASE key for the scenarios that exist in board.case_data (Arm C).
BOARD_CASE_MAP = {
    "hidden_clarithromycin_statin": "clarithromycin_simvastatin",
    "hidden_droperidol_qt": "qt_droperidol",
    "benign_carton_typo": "benign_lot",
}

RE_DERIVATION_COT = (
    "\n\nBefore answering, work through these steps explicitly:\n"
    "1. List every drug named (the signal drug and every co-prescribed drug).\n"
    "2. For each, state its major drug-drug interactions (CYP450, INR/anticoagulation, QT, bleeding).\n"
    "3. Cross-check whether any signal drug interacts dangerously with any co-prescribed drug.\n"
    "4. Compare severity against the recall headline."
)
DECISION_SUFFIX = (
    "\n\nEnd your response with a final line in EXACTLY this format:\n"
    "DECISION: ESCALATE   (a human must review)\n"
    "or\n"
    "DECISION: CONTINUE   (routine, no escalation)"
)

_DECISION_RE = re.compile(r"DECISION:\s*(ESCALATE|CONTINUE)", re.IGNORECASE)


def classify(text: str) -> str:
    m = _DECISION_RE.findall(text or "")
    if m:
        return m[-1].upper()
    t = (text or "").upper()
    return "ESCALATE" if "ESCALATE" in t else "CONTINUE"  # conservative fallback


def _signal_prompt(signal: str, cot: bool) -> str:
    body = INSTRUCTION + (RE_DERIVATION_COT if cot else "") + DECISION_SUFFIX
    return f"{body}\n\nINCOMING FDA SAFETY SIGNAL\n{signal}"


def run_single_arm(client, signal: str, cot: bool) -> tuple[str, int, float]:
    t0 = time.time()
    msg = client.messages.create(
        model=os.environ.get("BASELINE_MODEL", "claude-sonnet-4-5"),
        max_tokens=NAIVE_MAX_TOKENS,
        temperature=0,
        messages=[{"role": "user", "content": _signal_prompt(signal, cot)}],
    )
    dt = time.time() - t0
    text = msg.content[0].text
    tokens = (msg.usage.input_tokens or 0) + (msg.usage.output_tokens or 0)
    return classify(text), tokens, dt


async def run_board_arm(dsr_case: str) -> tuple[str, int, float]:
    """Arm C: full board. Tokens approximated from transcript length (labeled approx)."""
    from orchestrator import run_review
    from watch_room import fetch_transcript

    t0 = time.time()
    res = await run_review(case=dsr_case, clean=True)
    dt = time.time() - t0
    _, items, _ = await fetch_transcript(res["room_id"])
    chars = sum(len(m.content or "") for m in items)
    predicted = "ESCALATE" if res["final_verdict"] == "ESCALATE" else "CONTINUE"
    return predicted, chars // 4, dt  # ~4 chars/token, APPROX


def row(sid, stratum, expected, arm, rep, predicted, tokens, method, dt) -> dict:
    return {
        "Scenario_ID": sid, "Stratum": stratum, "Expected": expected, "Arm": arm,
        "Repeat": rep, "Predicted": predicted,
        "Hazard_Detected": predicted == "ESCALATE" and expected == "ESCALATE",
        "False_Escalation": predicted == "ESCALATE" and expected == "CONTINUE",
        "Tokens_Consumed": tokens, "Token_Method": method,
        "Execution_Time_s": round(dt, 2),
    }


async def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-board", action="store_true", help="also run Arm C (needs run_all.py up)")
    ap.add_argument("--limit", type=int, default=0, help="only run the first N scenarios")
    ap.add_argument("--out", default="ablation_results.csv")
    args = ap.parse_args()
    repeats = int(os.environ.get("REPEATS", "1"))

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY.")
    client = anthropic.Anthropic()

    scenarios = SCENARIOS[: args.limit] if args.limit else SCENARIOS
    rows: list[dict] = []
    for sid, stratum, expected, signal in scenarios:
        for rep in range(1, repeats + 1):
            pa, ta, da = run_single_arm(client, signal, cot=False)
            rows.append(row(sid, stratum, expected, "A_naive", rep, pa, ta, "exact", da))
            pb, tb, db = run_single_arm(client, signal, cot=True)
            rows.append(row(sid, stratum, expected, "B_cot_single", rep, pb, tb, "exact", db))
            print(f"  {sid:32s} rep{rep}  A={pa:8s} B={pb:8s} (exp {expected})")
            if args.with_board and sid in BOARD_CASE_MAP:
                pc, tc, dc = await run_board_arm(BOARD_CASE_MAP[sid])
                rows.append(row(sid, stratum, expected, "C_board", rep, pc, tc, "approx", dc))
                print(f"  {'':32s}       C={pc:8s} (board)")

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # quick summary
    print("\n=== summary (sensitivity = hazards caught; false-escalation = benign wrongly escalated) ===")
    for arm in sorted({r["Arm"] for r in rows}):
        sub = [r for r in rows if r["Arm"] == arm]
        haz = [r for r in sub if r["Expected"] == "ESCALATE"]
        ben = [r for r in sub if r["Expected"] == "CONTINUE"]
        sens = sum(r["Hazard_Detected"] for r in haz) / max(1, len(haz))
        fpr = sum(r["False_Escalation"] for r in ben) / max(1, len(ben))
        toks = sum(r["Tokens_Consumed"] for r in sub) / max(1, len(sub))
        print(f"  {arm:14s} sensitivity={sens:.2f}  false-escalation={fpr:.2f}  avg_tokens={toks:.0f}")
    print(f"\nWrote {len(rows)} rows -> {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
