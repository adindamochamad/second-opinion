"""
Register the Second Opinion review board agents on the Band platform.

Requires a User API key (not an agent key). Get one from app.band.ai under
your account settings, then put it in ``.env`` as ``THENVOI_API_KEY_USER``.

Usage:
    python setup_agents.py          # register all 3 agents
    python setup_agents.py --delete # tear down and clean up
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import os
import sys

import yaml
from dotenv import load_dotenv

from adapter_factory import agent_ids_path, credentials_path
from platform_url import get_platform_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

async def create_agents(agents: list[dict]) -> None:
    from thenvoi_rest import AsyncRestClient
    from thenvoi_rest.types import AgentRegisterRequest

    api_key = os.environ.get("THENVOI_API_KEY_USER")
    if not api_key:
        raise ValueError(
            "THENVOI_API_KEY_USER environment variable is required. "
            "Get a User API key from platform.thenvoi.com account settings."
        )

    config_file = credentials_path()
    ids_file = agent_ids_path()

    if config_file.exists():
        logger.info(
            "%s already exists — agents appear registered. "
            "Run 'python setup_agents.py --delete' first to rebuild.",
            config_file.name,
        )
        return

    client = AsyncRestClient(api_key=api_key, base_url=get_platform_url())

    config: dict[str, dict[str, str]] = {}
    agent_ids: list[str] = []

    for agent_def in agents:
        logger.info("Creating: %s ...", agent_def["name"])
        response = await client.human_api_agents.register_my_agent(
            agent=AgentRegisterRequest(
                name=agent_def["name"],
                description=agent_def["description"],
            )
        )

        agent = response.data.agent
        credentials = response.data.credentials

        config[agent_def["config_key"]] = {
            "agent_id": agent.id,
            "api_key": credentials.api_key,
        }
        agent_ids.append(agent.id)

        logger.info("  Created: %s (ID: %s)", agent.name, agent.id)

    with open(config_file, "w") as f:
        yaml.dump(config, f, default_flow_style=False)
    logger.info("Credentials written to %s", config_file.name)

    with open(ids_file, "w") as f:
        for aid in agent_ids:
            f.write(f"{aid}\n")
    logger.info("Agent IDs saved to %s for cleanup", ids_file.name)


async def delete_agents() -> None:
    from thenvoi_rest import AsyncRestClient

    api_key = os.environ.get("THENVOI_API_KEY_USER")
    if not api_key:
        raise ValueError("THENVOI_API_KEY_USER environment variable is required.")

    config_file = credentials_path()
    ids_file = agent_ids_path()

    if not ids_file.exists():
        logger.error("%s not found. Nothing to delete.", ids_file.name)
        sys.exit(1)

    client = AsyncRestClient(api_key=api_key, base_url=get_platform_url())

    with open(ids_file) as f:
        agent_ids = [line.strip() for line in f if line.strip()]

    for agent_id in agent_ids:
        try:
            await client.human_api_agents.delete_my_agent(id=agent_id, force=True)
            logger.info("Deleted agent: %s", agent_id)
        except Exception as e:
            logger.warning("Failed to delete %s: %s", agent_id, e)

    ids_file.unlink(missing_ok=True)
    config_file.unlink(missing_ok=True)
    logger.info("Cleanup complete.")


async def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Register (or delete) Second Opinion agents")
    parser.add_argument(
        "--delete", action="store_true",
        help="Tear down all registered agents",
    )
    args = parser.parse_args()

    if args.delete:
        await delete_agents()
        return

    scenario_mod = importlib.import_module("board.scenario")
    logger.info("Setting up Second Opinion review board agents...")
    await create_agents(scenario_mod.AGENTS)


if __name__ == "__main__":
    asyncio.run(main())
