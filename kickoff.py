"""
Kick off a Second Opinion review session.

Creates the review room, adds the agent participants, and posts the incoming
FDA safety signal that opens the review.

Uses the Agent API end to end — the Regulatory & Compliance intake desk creates
the room and adds the other agents, so the demo runs on the free tier (no
Enterprise / Human API required).

Usage:
    python kickoff.py                  # start a new review session
    python kickoff.py --no-clean       # skip leaving old rooms
    python kickoff.py --message "..."  # custom kickoff message
"""
from __future__ import annotations

import asyncio
import argparse
import importlib
import logging

import yaml
from dotenv import load_dotenv
from thenvoi_rest import AsyncRestClient, ChatMessageRequest, ParticipantRequest
from thenvoi_rest.types import ChatMessageRequestMentionsItem as Mention
from thenvoi_rest.types.chat_room_request import ChatRoomRequest

from adapter_factory import credentials_path
from platform_url import get_platform_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

async def _get_agent_identity(api_key: str) -> tuple[str, str, AsyncRestClient]:
    """Return (agent_id, agent_name, client) for an agent."""
    client = AsyncRestClient(api_key=api_key, base_url=get_platform_url())
    identity = await client.agent_api_identity.get_agent_me()
    return identity.data.id, identity.data.name, client


async def _leave_existing_rooms(name: str, agent_id: str, client: AsyncRestClient) -> None:
    """Best-effort: remove this agent from every room it is currently in.

    Keeps the room list tidy between demo runs. Uses only the Agent API.
    """
    try:
        chats = await client.agent_api_chats.list_agent_chats()
    except Exception as e:  # never let cleanup block a kickoff
        logger.warning("  Could not list rooms for %s: %s", name, e)
        return

    for room in chats.data or []:
        try:
            participants = await client.agent_api_participants.list_agent_chat_participants(room.id)
            for p in participants.data or []:
                # match self by agent id or name, then leave by participant id
                if p.id == agent_id or p.name == name:
                    await client.agent_api_participants.remove_agent_chat_participant(room.id, p.id)
        except Exception as e:
            logger.warning("  Could not leave room %s for %s: %s", room.id, name, e)


async def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Kick off a Second Opinion review session")
    parser.add_argument("--message", default=None, help="Custom kickoff message (overrides main room only)")
    parser.add_argument(
        "--no-clean", action="store_true",
        help="Skip leaving existing rooms before creating new ones",
    )
    args = parser.parse_args()

    config_file = credentials_path()
    if not config_file.exists():
        logger.error(
            "%s not found. Run 'python setup_agents.py' first.",
            config_file.name,
        )
        raise SystemExit(1)

    with open(config_file) as f:
        config = yaml.safe_load(f)

    scenario_mod = importlib.import_module("board.scenario")
    logger.info("Starting Second Opinion review session...")

    # ── Resolve agent identities (Agent API) ────────────────────────────
    agent_ids = {}    # config_key -> agent_id
    agent_names = {}  # config_key -> agent_name
    clients = {}      # config_key -> AsyncRestClient
    for agent_def in scenario_mod.AGENTS:
        key = agent_def["config_key"]
        aid, name, client = await _get_agent_identity(config[key]["api_key"])
        agent_ids[key] = aid
        agent_names[key] = name
        clients[key] = client
        logger.info("%s: %s (%s)", key, name, aid)

    # ── (optional) Leave old rooms so each run starts clean ─────────────
    if not args.no_clean:
        logger.info("Cleaning up old rooms...")
        for agent_def in scenario_mod.AGENTS:
            key = agent_def["config_key"]
            await _leave_existing_rooms(agent_names[key], agent_ids[key], clients[key])
        logger.info("Cleanup complete.")

    # ── Get kickoff config from scenario ────────────────────────────────
    kickoff_config = scenario_mod.get_kickoff_config(agent_ids, agent_names)

    # ── Create rooms (as the owner agent) and post the signal ───────────
    room_ids = {}
    for room_def in kickoff_config["rooms"]:
        owner_key = room_def["owner"]
        owner_client = clients[owner_key]

        # Owner agent creates the room (it becomes the room owner/participant).
        room = await owner_client.agent_api_chats.create_agent_chat(chat=ChatRoomRequest())
        room_id = room.data.id
        room_ids[room_def["name"]] = room_id
        logger.info("Created %s room: %s (owner: %s)", room_def["name"], room_id, owner_key)

        # Add the other agents (skip the owner — already in as creator).
        for participant_key in room_def["participants"]:
            if participant_key == owner_key:
                continue
            await owner_client.agent_api_participants.add_agent_chat_participant(
                room_id,
                participant=ParticipantRequest(participant_id=agent_ids[participant_key]),
            )
            logger.info("  Added %s", participant_key)

        # Compose and send the kickoff message via the owner agent.
        message = room_def["message"]
        if args.message and room_def["name"] == "main":
            mention_names = " ".join(f"@{agent_names[k]}" for k in room_def["mentions"])
            message = f"{mention_names} {args.message}"

        mentions = [
            Mention(id=agent_ids[k], name=agent_names[k])
            for k in room_def["mentions"]
        ]
        await owner_client.agent_api_messages.create_agent_chat_message(
            room_id,
            message=ChatMessageRequest(content=message, mentions=mentions),
        )
        logger.info("  Sent kickoff message to %s room", room_def["name"])

    # ── Print room summary ──────────────────────────────────────────────
    print("\n" + "=" * 56)
    print("  SECOND OPINION — REVIEW ROOMS")
    print("=" * 56)
    for name, rid in room_ids.items():
        print(f"  {name:20s} {rid}")
    print("=" * 56 + "\n")
    print("Open the room in the Band web UI to watch the agents deliberate.\n")


if __name__ == "__main__":
    asyncio.run(main())
