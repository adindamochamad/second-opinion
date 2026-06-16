"""Safety Verifier — independent second opinion (cross-framework challenger).

Runs on a DIFFERENT framework from the Clinical Reviewer on purpose: an
independent challenger, not an echo. Its job is to re-derive the hazard,
check sources/confidence, surface what the headline hides, and escalate on
disagreement. Framework/model configured in board/agents.yaml.
"""
from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv

from thenvoi import Agent

from adapter_factory import (
    TUPLE_TOOL_FRAMEWORKS,
    create_adapter,
    load_credentials,
    resolve_framework,
)
from platform_url import get_platform_url, get_ws_url
from board.review_prompts import verifier_prompt
from self_aware_preprocessor import SelfAwarePreprocessor
from tools.fda_tools import build_fda_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("safety_verifier")


async def main() -> None:
    load_dotenv()
    from tool_filter import remove_tools

    remove_tools("thenvoi_add_participant", "thenvoi_lookup_peers", "thenvoi_create_chatroom")

    agent_id, api_key = load_credentials("safety_verifier")

    # The Verifier pulls live openFDA labels + PubMed to *source* its independent
    # re-derivation (USE_LIVE_TOOLS=1). Tuple-format tools only attach on adapters
    # that accept them; on a langgraph/Groq Verifier the re-derivation still runs
    # from the model's own pharmacology knowledge, just without live citations.
    tools = build_fda_tools()
    if tools and resolve_framework("safety_verifier") not in TUPLE_TOOL_FRAMEWORKS:
        logger.info("Live tools not attached on this framework; re-deriving from model knowledge.")
        tools = []
    adapter = create_adapter("safety_verifier", verifier_prompt(), additional_tools=tools)

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=get_ws_url(),
        rest_url=get_platform_url(),
        preprocessor=SelfAwarePreprocessor(),
    )
    logger.info("Safety Verifier agent is online.")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
