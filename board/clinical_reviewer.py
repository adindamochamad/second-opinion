"""Clinical Reviewer — lead assessor (Claude via the Anthropic adapter).

Drafts the first patient-safety assessment from the live FDA signal, then is
REQUIRED to request an independent second opinion before anything is final.
Framework/model configured in board/agents.yaml.
"""
from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv

from thenvoi import Agent

from adapter_factory import create_adapter, load_credentials
from platform_url import get_platform_url, get_ws_url
from board.review_prompts import clinical_prompt
from self_aware_preprocessor import SelfAwarePreprocessor
from tools.fda_tools import build_fda_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("clinical_reviewer")


async def main() -> None:
    load_dotenv()
    from tool_filter import remove_tools

    remove_tools("thenvoi_add_participant", "thenvoi_lookup_peers", "thenvoi_create_chatroom")

    agent_id, api_key = load_credentials("clinical_reviewer")
    adapter = create_adapter(
        "clinical_reviewer", clinical_prompt(), additional_tools=build_fda_tools()
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=get_ws_url(),
        rest_url=get_platform_url(),
        preprocessor=SelfAwarePreprocessor(),
    )
    logger.info("Clinical Reviewer agent is online.")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
