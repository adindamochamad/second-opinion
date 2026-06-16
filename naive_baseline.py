"""Naive single-agent baseline — compute-equalized "before" arm.

Identical input to the board (build_incoming_signal). NEUTRAL instruction (no
"be brief / one paragraph" handicap) and a token budget matched to the board's
aggregate, so the only variable vs the board is ARCHITECTURE, not compute volume.

Run:  python naive_baseline.py            # default case (amiodarone_warfarin)
      DSR_CASE=benign_lot python naive_baseline.py
(Needs ANTHROPIC_API_KEY. Wording varies by run; the point is the missing second
opinion, not a scripted answer.)
"""
from __future__ import annotations

import os

import anthropic
from dotenv import load_dotenv

from board.case_data import build_incoming_signal

# Neutral, professional framing — no nudge toward shallow/decisive answers.
INSTRUCTION = (
    "You are a clinical pharmacology reviewer. Analyze this incoming drug-safety / "
    "regulatory signal for any potential drug-drug interactions or patient hazards in the "
    "described population. Reason as carefully and thoroughly as the case warrants, then "
    "give your assessment and a final recommendation."
)

# Match the board's aggregate output budget (~3 agents x ~1.3k tokens), so the
# baseline is not starved of reasoning room. Override with BASELINE_MAX_TOKENS.
NAIVE_MAX_TOKENS = int(os.environ.get("BASELINE_MAX_TOKENS", "4000"))


def build_naive_prompt(case: str | None = None) -> str:
    """The same intake signal the board sees, framed as a single neutral pass."""
    # DSR_LIVE=0 -> frozen text (matches the board's frozen kickoff for recording).
    live = os.environ.get("DSR_LIVE", "1") != "0"
    signal = build_incoming_signal(case=case, live=live)
    return f"{INSTRUCTION}\n\n{signal}"


def run_naive(case: str | None = None) -> str:
    """Return the naive single agent's recommendation text (one model, one pass)."""
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY (or add it to .env) to run the baseline.")

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.environ.get("BASELINE_MODEL", "claude-sonnet-4-5"),
        max_tokens=NAIVE_MAX_TOKENS,
        messages=[{"role": "user", "content": build_naive_prompt(case)}],
    )
    return msg.content[0].text


def main() -> None:
    print("=" * 64)
    print("  NAIVE SINGLE AGENT — neutral instruction, compute-matched")
    print("  (same intake signal the board receives)")
    print("=" * 64)
    print(run_naive())
    print("=" * 64)


if __name__ == "__main__":
    main()
