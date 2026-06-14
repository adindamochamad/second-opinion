"""The side-by-side demo: one naive agent vs the Band review board.

LEFT  (before): a single agent triages the FDA recall headline and answers from
                one pass — no second opinion, no escalation, no audit trail.
RIGHT (after):  the same signal goes through the Band room, where an independent
                Safety Verifier challenges the assessment, surfaces the warfarin
                interaction the headline hides, and the board escalates to a human
                with a full decision packet.

Run order for a recording:
    1) python run_all.py                 # start the 3 agents
    2) python kickoff.py                 # post the FDA signal (DSR_LIVE=0 for frozen case)
    3) python demo.py                    # show naive vs the board's latest room

    python demo.py <room_id>             # pin a specific board room
"""
from __future__ import annotations

import argparse
import asyncio
import textwrap

from dotenv import load_dotenv

from naive_baseline import run_naive
from watch_room import fetch_transcript, resolve_mentions

RULE = "=" * 72


def _wrap(text: str, indent: str = "  ") -> str:
    out = []
    for para in (text or "").split("\n"):
        if not para.strip():
            out.append("")
            continue
        out.append(textwrap.fill(para, width=70, initial_indent=indent, subsequent_indent=indent))
    return "\n".join(out)


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Side-by-side naive vs Band board demo")
    parser.add_argument("room_id", nargs="?", default=None, help="board room (default: latest)")
    args = parser.parse_args()

    print("\n" + RULE)
    print("  SECOND OPINION — one agent vs a review board on Band")
    print(RULE)

    # ── BEFORE ──────────────────────────────────────────────────────────
    print("\n\n>>> BEFORE — NAIVE SINGLE AGENT (one pass, no one to check it)\n")
    print(_wrap(run_naive()))
    print("\n  ^ Confident. Decisive. No independent check, no escalation, no audit.")

    # ── AFTER ───────────────────────────────────────────────────────────
    print("\n\n" + RULE)
    print(">>> AFTER — THE REVIEW BOARD (collaborating through Band)")
    print(RULE)
    room_id, items, id2name = await fetch_transcript(args.room_id)
    if not room_id or not items:
        print("\n  No board room found. Run:  python run_all.py  then  python kickoff.py")
        return

    visible = [m for m in items if (m.message_type or "").lower() not in ("thought", "event")]
    print(f"\n  Band room {room_id} — {len(visible)} messages, mention-routed:\n")
    for m in visible:
        print(f"\n  --- {m.sender_name} ---")
        print(_wrap(resolve_mentions(m.content, id2name), indent="    "))

    print("\n\n" + RULE)
    print("  The naive agent answered alone. The board cross-examined, surfaced the")
    print("  hazard the headline hid, and escalated to a human — on Band.")
    print(RULE + "\n")


if __name__ == "__main__":
    asyncio.run(main())
