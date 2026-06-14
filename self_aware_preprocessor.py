"""Custom preprocessor that marks the agent's own entry in the participant list.

The SDK's default participant message does not indicate which entry is the
agent itself.  Without this marker, LLMs frequently confuse their identity
and attempt to @mention themselves, which the Thenvoi API rejects with
``422: Cannot mention yourself in a message``.

Usage::

    from self_aware_preprocessor import SelfAwarePreprocessor

    agent = Agent.create(
        ...,
        preprocessor=SelfAwarePreprocessor(),
    )
"""
from __future__ import annotations

import dataclasses
import re

from thenvoi.core.types import AgentInput
from thenvoi.preprocessing.default import DefaultPreprocessor


_YOU_MARKER = " (You)"
_SELF_MENTION_WARNING = " NEVER mention yourself (the entry marked '(You)')."


class SelfAwarePreprocessor:
    """Wraps DefaultPreprocessor; marks self with '(You)' and hides non-Agent participants."""

    def __init__(self) -> None:
        self._default = DefaultPreprocessor()

    async def process(self, ctx, event, agent_id: str) -> AgentInput | None:
        result = await self._default.process(ctx, event, agent_id)
        if result is None or result.participants_msg is None:
            return result

        agent_handle = _resolve_handle(agent_id, ctx.participants)
        non_agent_handles = _non_agent_handles(ctx.participants)
        new_msg = result.participants_msg
        if agent_handle:
            new_msg = _mark_self(new_msg, agent_handle)
        if non_agent_handles:
            new_msg = _strip_participants(new_msg, non_agent_handles)
        if new_msg != result.participants_msg:
            result = dataclasses.replace(result, participants_msg=new_msg)
        return result


def _non_agent_handles(participants: list[dict]) -> set[str]:
    """Return handles of non-Agent participants (humans, etc.)."""
    return {
        p["handle"]
        for p in participants
        if p.get("type") != "Agent" and p.get("handle")
    }


def _strip_participants(msg: str, handles: set[str]) -> str:
    """Remove participant lines for the given handles."""
    lines = msg.split("\n")
    filtered = [
        line for line in lines
        if not any(f"@{h} " in line for h in handles)
    ]
    return "\n".join(filtered)


def _resolve_handle(agent_id: str, participants: list[dict]) -> str | None:
    for p in participants:
        if p.get("id") == agent_id:
            return p.get("handle")
    return None


def _mark_self(msg: str, agent_handle: str) -> str:
    # Add "(You)" after the agent's own line in the participant list
    pattern = re.compile(
        rf"(- @{re.escape(agent_handle)} .+?)(\n|$)"
    )
    msg = pattern.sub(rf"\g<1>{_YOU_MARKER}\2", msg, count=1)

    # Append self-mention warning to the IMPORTANT instruction line
    if "NEVER mention yourself" not in msg:
        msg = msg.replace(
            "Handles are lowercase with no spaces.",
            "Handles are lowercase with no spaces." + _SELF_MENTION_WARNING,
        )
    return msg
