"""Per-process tool filtering for Thenvoi agents.

The Thenvoi SDK does not yet support an ``excluded_tools`` parameter on
adapters or ``Agent.create()``. As a workaround we:

1. **Always** pop the excluded tools from ``thenvoi.runtime.tools.TOOL_DEFINITIONS``.
   That dict is the source of truth for ``AgentTools.get_tool_schemas`` (OpenAI
   and Anthropic formats), which means a single pop filters codex, anthropic,
   pydantic_ai, claude_sdk, gemini, and google_adk all at once.

2. **Conditionally** monkey-patch the LangGraph and CrewAI adapters, because
   those two build their tool lists outside ``TOOL_DEFINITIONS``. The patches
   are skipped cleanly when the framework isn't installed (e.g. Python 3.14
   where crewai has no wheel yet).

Each agent runs in its own ``multiprocessing.Process`` (via ``run_all.py``),
so mutations here are isolated per agent.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_EXCLUDED: set[str] = set()
_patched = False


def remove_tools(*tool_names: str) -> None:
    """Remove named tools from all adapter tool lists for this process."""
    _EXCLUDED.update(tool_names)
    _patch_once()

    # Always prune the runtime catalog. This is what filters codex and every
    # other adapter that reads via AgentToolsProtocol.get_tool_schemas.
    from thenvoi.runtime.tools import TOOL_DEFINITIONS

    for name in tool_names:
        TOOL_DEFINITIONS.pop(name, None)


def _patch_once() -> None:
    global _patched
    if _patched:
        return
    _patched = True

    _patch_langgraph()
    _patch_crewai()


def _patch_langgraph() -> None:
    """LangGraph builds wrappers manually — filter the output list."""
    try:
        import thenvoi.integrations.langgraph.langchain_tools as _lg_mod
        import thenvoi.adapters.langgraph as _lg_adapter_mod
    except ImportError:
        logger.debug("langgraph not installed; skipping tool-filter patch")
        return

    _orig_lg = _lg_mod.agent_tools_to_langchain

    def _filtered_lg(*args, **kwargs):
        tools = _orig_lg(*args, **kwargs)
        return [t for t in tools if t.name not in _EXCLUDED]

    _lg_mod.agent_tools_to_langchain = _filtered_lg
    # The adapter imports the symbol at call time, so patch the module it reads from.
    _lg_adapter_mod.agent_tools_to_langchain = _filtered_lg  # type: ignore[attr-defined]


def _patch_crewai() -> None:
    """CrewAI has its own tool builder and crashes on empty LLM responses."""
    try:
        from thenvoi.adapters.crewai import CrewAIAdapter
    except ImportError:
        logger.debug("crewai not installed; skipping tool-filter patch")
        return

    _orig_crewai = CrewAIAdapter._create_crewai_tools

    def _filtered_crewai(self):
        tools = _orig_crewai(self)
        return [t for t in tools if t.name not in _EXCLUDED]

    CrewAIAdapter._create_crewai_tools = _filtered_crewai  # type: ignore[assignment]

    # CrewAI rejects empty LLM responses with ValueError. When an agent is
    # instructed to stay silent (not @mentioned) the model sometimes returns
    # nothing instead of calling a tool. Substitute a no-op string so the
    # executor treats it as a harmless final answer.
    try:
        from crewai.utilities import agent_utils as _agent_utils_mod
    except ImportError:
        return

    _orig_validate = _agent_utils_mod._validate_and_finalize_llm_response

    def _safe_validate(answer, executor_context, printer, verbose=True):
        if not answer:
            answer = "[no response]"
        return _orig_validate(answer, executor_context, printer, verbose=verbose)

    _agent_utils_mod._validate_and_finalize_llm_response = _safe_validate
