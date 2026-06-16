"""Supervisor orchestrator for the Second Opinion review board.

Code-level control flow (not prompt narration):
  intake -> Clinical assessment -> Verifier verdict
    [CHALLENGE] -> Clinical [REVISED ASSESSMENT] -> Verifier final verdict   (capped: 1 round)
  branch on final verdict:
    [ROUTINE]  -> Clinical posts ROUTINE CLOSURE; Regulatory is BYPASSED (saves a hop).
    [ESCALATE] -> Regulatory compliance read -> Clinical DECISION PACKET for human.

The Review Coordinator (a registered, non-LLM identity) is the ONLY thing that
@mentions specialists. Specialists only ever address the Coordinator.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re

import yaml
from dotenv import load_dotenv
from thenvoi_rest import AsyncRestClient, ChatMessageRequest, ParticipantRequest
from thenvoi_rest.types import ChatMessageRequestMentionsItem as Mention
from thenvoi_rest.types.chat_room_request import ChatRoomRequest

from adapter_factory import credentials_path
from board.case_data import build_incoming_signal, get_scenario
from platform_url import get_platform_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s [orchestrator] %(message)s")
logger = logging.getLogger("orchestrator")

CLINICAL_KEY = "clinical_reviewer"
VERIFIER_KEY = "safety_verifier"
REGULATORY_KEY = "regulatory_compliance"
COORD_KEY = "review_coordinator"

_VERDICT_RE = re.compile(r"\[VERDICT:\s*(CHALLENGE|ROUTINE|ESCALATE)\]", re.IGNORECASE)
TURN_TIMEOUT = float(os.environ.get("DSR_TURN_TIMEOUT", "200"))  # seconds per agent turn
POLL_EVERY = 2.0


def parse_verdict(text: str) -> str | None:
    """Return the last verdict token in `text` (CHALLENGE/ROUTINE/ESCALATE) or None."""
    matches = _VERDICT_RE.findall(text or "")
    return matches[-1].upper() if matches else None


async def _identity(api_key: str) -> tuple[str, str, AsyncRestClient]:
    client = AsyncRestClient(api_key=api_key, base_url=get_platform_url())
    me = await client.agent_api_identity.get_agent_me()
    return me.data.id, me.data.name, client


async def _post(client, room_id, text, mention_id, mention_name) -> None:
    content = f"@{mention_name} {text}"
    await client.agent_api_messages.create_agent_chat_message(
        room_id,
        message=ChatMessageRequest(
            content=content, mentions=[Mention(id=mention_id, name=mention_name)]
        ),
    )


async def _wait_for(client, room_id, sender_substr, cursor) -> tuple[str, str]:
    """Block until a visible message from `sender_substr` newer than `cursor` appears.

    Returns (content, new_cursor). The coordinator does not see its own posts, so
    polling its message list yields only the specialists' replies. `cursor` is the
    ISO `inserted_at` of the last message we consumed (string compare == chronological).
    """
    deadline = asyncio.get_event_loop().time() + TURN_TIMEOUT
    needle = sender_substr.lower()
    while asyncio.get_event_loop().time() < deadline:
        msgs = await client.agent_api_messages.list_agent_messages(
            room_id, status="all", page_size=100
        )
        hits = [
            m for m in (msgs.data or [])
            if (m.message_type or "").lower() not in ("thought", "event")
            and needle in (m.sender_name or "").lower()
            and str(m.inserted_at or "") > cursor
        ]
        if hits:
            hits.sort(key=lambda m: str(m.inserted_at or ""))
            latest = hits[-1]
            return latest.content or "", str(latest.inserted_at or "")
        await asyncio.sleep(POLL_EVERY)
    raise TimeoutError(f"No reply from '{sender_substr}' within {TURN_TIMEOUT}s.")


async def run_review(*, case=None, clean=True, room_ready: "asyncio.Future | None" = None) -> dict:
    """Drive one review to completion. `room_ready` (optional) is resolved with the
    room id as soon as the room exists, so a web layer can start streaming early."""
    if case:
        os.environ["DSR_CASE"] = case
    scenario = get_scenario(case)

    with open(credentials_path()) as f:
        cfg = yaml.safe_load(f)
    for key in (CLINICAL_KEY, VERIFIER_KEY, REGULATORY_KEY, COORD_KEY):
        if key not in cfg:
            raise SystemExit(
                f"'{key}' missing from agent_config.yaml. Register the Review Coordinator "
                "(register_coordinator.py) or re-run setup_agents.py after adding it to "
                "board/scenario.AGENTS."
            )

    ids, names, clients = {}, {}, {}
    for key in (CLINICAL_KEY, VERIFIER_KEY, REGULATORY_KEY, COORD_KEY):
        ids[key], names[key], clients[key] = await _identity(cfg[key]["api_key"])

    coord = clients[COORD_KEY]
    room = await coord.agent_api_chats.create_agent_chat(chat=ChatRoomRequest())
    room_id = room.data.id
    logger.info("Room %s created (case=%s, expected=%s)", room_id, scenario.key, scenario.expected)
    if room_ready is not None and not room_ready.done():
        room_ready.set_result(room_id)

    for key in (CLINICAL_KEY, VERIFIER_KEY, REGULATORY_KEY):
        await coord.agent_api_participants.add_agent_chat_participant(
            room_id, participant=ParticipantRequest(participant_id=ids[key])
        )

    live = os.environ.get("DSR_LIVE", "1") != "0"
    signal = build_incoming_signal(live=live)
    cursor = ""
    challenged = False

    # NOTE: Band delivers each agent only the turns that @mention it. Because every
    # specialist addresses the Coordinator (never each other), the Coordinator MUST
    # carry the prior agent's content forward in each routing message — otherwise the
    # Verifier cannot see the assessment it is asked to verify. We thread it explicitly.

    # ── Step 1: intake -> Clinical assessment ───────────────────────────
    await _post(
        coord, room_id,
        f"{signal}\n\nAssess this signal with sourced, confidence-tagged claims and a draft "
        "recommendation, then report back to me. Do not contact other specialists directly.",
        ids[CLINICAL_KEY], names[CLINICAL_KEY],
    )
    logger.info("-> Clinical (intake). Waiting for assessment...")
    clin_text, cursor = await _wait_for(coord, room_id, "Clinical", cursor)

    # ── Step 2: Verifier independent verdict (assessment carried forward) ─
    await _post(
        coord, room_id,
        "Here is the Clinical Reviewer's assessment to verify independently:\n\n"
        f"-----\n{clin_text}\n-----\n\nIndependently re-derive the pharmacology, then return "
        "your verdict token. Report back to me.",
        ids[VERIFIER_KEY], names[VERIFIER_KEY],
    )
    logger.info("-> Verifier (review). Waiting for verdict...")
    v_text, cursor = await _wait_for(coord, room_id, "Verifier", cursor)
    verdict = parse_verdict(v_text) or "ESCALATE"  # fail safe toward human review
    logger.info("Verifier verdict #1: %s", verdict)

    # ── Step 3: CHALLENGE -> revise -> re-verify (capped at 1 round) ─────
    if verdict == "CHALLENGE":
        challenged = True
        await _post(
            coord, room_id,
            "[VERDICT: CHALLENGE] The Safety Verifier challenged your assessment. Critique:\n\n"
            f"-----\n{v_text}\n-----\n\nRe-evaluate the source data, address each point, and post "
            "a [REVISED ASSESSMENT]. Report back to me.",
            ids[CLINICAL_KEY], names[CLINICAL_KEY],
        )
        logger.info("-> Clinical (revise). Waiting for [REVISED ASSESSMENT]...")
        clin_text, cursor = await _wait_for(coord, room_id, "Clinical", cursor)

        await _post(
            coord, room_id,
            "The Clinical Reviewer posted a revised assessment:\n\n"
            f"-----\n{clin_text}\n-----\n\nRe-verify it. Return ONLY [VERDICT: ROUTINE] or "
            "[VERDICT: ESCALATE] — no further challenge. Report back to me.",
            ids[VERIFIER_KEY], names[VERIFIER_KEY],
        )
        logger.info("-> Verifier (re-verify). Waiting for final verdict...")
        v_text, cursor = await _wait_for(coord, room_id, "Verifier", cursor)
        verdict = parse_verdict(v_text) or "ESCALATE"
        if verdict == "CHALLENGE":  # cap: unresolved disagreement -> human
            verdict = "ESCALATE"
        logger.info("Verifier verdict #2 (post-revision): %s", verdict)

    # ── Step 4: REAL branch on final verdict ────────────────────────────
    if verdict == "ROUTINE":
        await _post(
            coord, room_id,
            "[VERDICT: ROUTINE] The board stood down. Post a brief ROUTINE CLOSURE (final "
            "recommendation CONTINUE) for the record. Regulatory escalation is bypassed.",
            ids[CLINICAL_KEY], names[CLINICAL_KEY],
        )
        logger.info("-> Clinical (routine closure). Regulatory BYPASSED.")
        _, cursor = await _wait_for(coord, room_id, "Clinical", cursor)
        path = ["intake", "assessment", "verify"]
        path += ["revise", "re-verify"] if challenged else []
        path += ["routine-closure"]
    else:
        await _post(
            coord, room_id,
            "[VERDICT: ESCALATE] This case is being escalated. The Clinical assessment and the "
            f"Verifier finding follow.\n\nASSESSMENT:\n-----\n{clin_text}\n-----\n\nVERIFIER:\n"
            f"-----\n{v_text}\n-----\n\nProvide the regulatory/compliance read (classification, "
            "21 CFR 314.80 reporting duties, labeling) with sources, then report to me.",
            ids[REGULATORY_KEY], names[REGULATORY_KEY],
        )
        logger.info("-> Regulatory (compliance read). Waiting...")
        reg_text, cursor = await _wait_for(coord, room_id, "Regulatory", cursor)
        await _post(
            coord, room_id,
            "Compile the DECISION PACKET FOR HUMAN REVIEWER now (visible message): the original "
            "claim, the Verifier's challenge/verdict, the compliance read, all sources, and final "
            f"recommendation ESCALATE-TO-HUMAN.\n\nVERIFIER FINDING:\n-----\n{v_text}\n-----\n\n"
            f"COMPLIANCE READ:\n-----\n{reg_text}\n-----",
            ids[CLINICAL_KEY], names[CLINICAL_KEY],
        )
        logger.info("-> Clinical (decision packet). Waiting...")
        _, cursor = await _wait_for(coord, room_id, "Clinical", cursor)
        path = ["intake", "assessment", "verify"]
        path += ["revise", "re-verify"] if challenged else []
        path += ["compliance", "decision-packet"]

    result = {
        "room_id": room_id, "final_verdict": verdict, "challenged": challenged,
        "expected": scenario.expected, "path": path,
    }
    logger.info("DONE: verdict=%s challenged=%s path=%s", verdict, challenged, "->".join(path))
    return result


async def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Drive one supervised review")
    ap.add_argument("--case", default=None, help="DSR_CASE scenario key")
    ap.add_argument("--no-clean", action="store_true")
    args = ap.parse_args()
    res = await run_review(case=args.case, clean=not args.no_clean)
    print("\n" + "=" * 56)
    for k, v in res.items():
        print(f"  {k:14s} {v}")
    print("=" * 56)


if __name__ == "__main__":
    asyncio.run(main())
