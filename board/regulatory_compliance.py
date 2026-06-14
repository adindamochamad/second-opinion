"""Regulatory and Compliance Officer — intake + final compliance gate.

Posts the intake signal, then — once the clinical hazard is settled — adds
the regulatory layer and decides ROUTINE vs ESCALATE.
Framework/model configured in board/agents.yaml.
"""
from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv

from thenvoi import Agent

from adapter_factory import create_adapter, load_credentials
from platform_url import get_platform_url, get_ws_url
from board.review_prompts import regulatory_prompt
from self_aware_preprocessor import SelfAwarePreprocessor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("regulatory_compliance")


async def main() -> None:
    load_dotenv()
    from tool_filter import remove_tools

    remove_tools("thenvoi_add_participant", "thenvoi_lookup_peers", "thenvoi_create_chatroom")

    agent_id, api_key = load_credentials("regulatory_compliance")
    adapter = create_adapter("regulatory_compliance", regulatory_prompt())

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=get_ws_url(),
        rest_url=get_platform_url(),
        preprocessor=SelfAwarePreprocessor(),
    )
    logger.info("Regulatory and Compliance Officer agent is online.")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
