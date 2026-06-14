"""Naive single-agent baseline — the "before" half of the side-by-side demo.

One model, one pass, no independent verifier, no escalation path, no audit
trail. It receives the recall headline the way a busy triage assistant would and
returns a quick recommendation. This is what the review board is compared
against: not a worse model, but the *absence of cross-examination*.

Run:  python naive_baseline.py
(Needs ANTHROPIC_API_KEY. Wording varies by run; the point is the missing
second opinion, not a scripted answer.)
"""
from __future__ import annotations

import os

import anthropic
from dotenv import load_dotenv

# The headline a single agent triages — limited-lot recall, looks low-impact.
# (The review board additionally surfaces that amiodarone raises INR in
# co-prescribed warfarin patients; this naive path has no one to surface that.)
HEADLINE = """\
FDA drug enforcement report: Amiodarone HCl 200 mg tablets, Class II recall.
Reason: subpotent / out-of-specification results in 3 distributed lots.
Status: ongoing. Distribution: multi-state hospital and retail pharmacy.

We have amiodarone on our cardiology formulary. Quick recommendation: do we need
to take any action, or can we continue as normal?"""

PROMPT = (
    "You are a clinical triage assistant. Give a brief, decisive recommendation "
    "in one short paragraph.\n\n" + HEADLINE
)


def run_naive() -> str:
    """Return the naive single agent's recommendation text (one model, one pass)."""
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY (or add it to .env) to run the baseline.")

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.environ.get("BASELINE_MODEL", "claude-sonnet-4-5"),
        max_tokens=500,
        messages=[{"role": "user", "content": PROMPT}],
    )
    return msg.content[0].text


def main() -> None:
    print("=" * 64)
    print("  NAIVE SINGLE AGENT — no second opinion, no escalation, no audit")
    print("=" * 64)
    print(run_naive())
    print("=" * 64)


if __name__ == "__main__":
    main()
