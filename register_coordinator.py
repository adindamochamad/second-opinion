"""Register ONLY the Review Coordinator and append it to agent_config.yaml.

The Coordinator is a non-LLM supervisor identity used by orchestrator.py to route
turns. This appends to the existing credentials file instead of deleting and
rebuilding all agents (which would orphan existing rooms). Idempotent: if the
coordinator key is already present, it does nothing.

Usage:
    python register_coordinator.py
"""
from __future__ import annotations

import asyncio
import logging

import yaml
from dotenv import load_dotenv

from adapter_factory import agent_ids_path, credentials_path
from board.scenario import COORDINATOR_KEY, COORDINATOR_NAME
from platform_url import get_platform_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()
    import os
    from thenvoi_rest import AsyncRestClient
    from thenvoi_rest.types import AgentRegisterRequest

    api_key = os.environ.get("THENVOI_API_KEY_USER")
    if not api_key:
        raise SystemExit("THENVOI_API_KEY_USER is required (User API key from app.band.ai).")

    config_file = credentials_path()
    if not config_file.exists():
        raise SystemExit(f"{config_file.name} not found — run setup_agents.py for the 3 agents first.")

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}

    if COORDINATOR_KEY in config:
        logger.info("%s already registered (%s). Nothing to do.", COORDINATOR_KEY,
                    config[COORDINATOR_KEY].get("agent_id"))
        return

    client = AsyncRestClient(api_key=api_key, base_url=get_platform_url())
    resp = await client.human_api_agents.register_my_agent(
        agent=AgentRegisterRequest(
            name=COORDINATOR_NAME,
            description="Non-LLM supervisor that routes turns and enforces the review protocol.",
        )
    )
    agent = resp.data.agent
    creds = resp.data.credentials
    config[COORDINATOR_KEY] = {"agent_id": agent.id, "api_key": creds.api_key}

    with open(config_file, "w") as f:
        yaml.dump(config, f, default_flow_style=False)
    logger.info("Registered %s (%s) and appended to %s", agent.name, agent.id, config_file.name)

    # Track the id for cleanup alongside the others.
    ids_file = agent_ids_path()
    if ids_file.exists():
        with open(ids_file, "a") as f:
            f.write(f"{agent.id}\n")


if __name__ == "__main__":
    asyncio.run(main())
