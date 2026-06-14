"""Print the transcript of a drug-safety review room (sender + message).

Reads the room via the Agent API using the agent keys, so it works on the
free/hackathon tier. Resolves @[[agent-id]] mention tokens to @Name and merges
every agent's view (an agent's own posts are omitted from its own list) so the
full transcript shows. With no argument it picks the most recent room.

Usage:
    python watch_room.py                 # latest room
    python watch_room.py <room_id>       # a specific room
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys

import yaml
from dotenv import load_dotenv
from thenvoi_rest import AsyncRestClient

from adapter_factory import credentials_path
from platform_url import get_platform_url

OWNER_KEY = "regulatory_compliance"
_MENTION = re.compile(r"@\[\[([0-9a-fA-F-]+)\]\]")


def _clients() -> dict[str, AsyncRestClient]:
    with open(credentials_path()) as f:
        config = yaml.safe_load(f)
    return {
        k: AsyncRestClient(api_key=v["api_key"], base_url=get_platform_url())
        for k, v in config.items()
    }


async def fetch_transcript(room_id: str | None = None):
    """Return (room_id, sorted_messages, id->name map). room_id=None -> latest."""
    clients = _clients()
    owner = clients.get(OWNER_KEY) or next(iter(clients.values()))

    if not room_id:
        chats = await owner.agent_api_chats.list_agent_chats()
        rooms = sorted(chats.data or [], key=lambda r: str(r.inserted_at or ""), reverse=True)
        if not rooms:
            return None, [], {}
        room_id = rooms[0].id

    # id -> name from the participant list (covers every agent in the room)
    id2name: dict[str, str] = {}
    try:
        parts = await owner.agent_api_participants.list_agent_chat_participants(room_id)
        for p in parts.data or []:
            id2name[p.id] = p.name
    except Exception:
        pass

    # An agent's message list omits its own posts; merge all views, dedup by id.
    merged: dict[str, object] = {}
    for c in clients.values():
        try:
            msgs = await c.agent_api_messages.list_agent_messages(room_id, status="all", page_size=100)
            for m in msgs.data or []:
                merged[m.id] = m
                if m.sender_id and m.sender_name:
                    id2name.setdefault(m.sender_id, m.sender_name)
        except Exception:
            continue
    items = sorted(merged.values(), key=lambda m: str(m.inserted_at or ""))
    return room_id, items, id2name


def resolve_mentions(text: str, id2name: dict[str, str]) -> str:
    return _MENTION.sub(lambda m: "@" + id2name.get(m.group(1), "someone"), text or "")


def render_transcript(room_id: str, items: list, id2name: dict[str, str]) -> str:
    lines = ["=" * 72, f"  ROOM {room_id}  ({len(items)} messages)", "=" * 72]
    for m in items:
        if (m.message_type or "").lower() in ("thought", "event"):
            continue
        ts = str(m.inserted_at or "")[11:19]
        lines.append(f"\n[{ts}] {m.sender_name}:")
        lines.append(resolve_mentions(m.content, id2name))
    lines.append("\n" + "=" * 72)
    return "\n".join(lines)


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Print a review room transcript")
    parser.add_argument("room_id", nargs="?", default=None)
    args = parser.parse_args()

    room_id, items, id2name = await fetch_transcript(args.room_id)
    if not room_id:
        print("No rooms found. Run kickoff.py first.")
        sys.exit(1)
    print(render_transcript(room_id, items, id2name))


if __name__ == "__main__":
    asyncio.run(main())
