"""Build the correct Band adapter for an agent from the scenario's agents.yaml.

Extends the Band legal-demo factory with explicit per-agent ``provider``,
``base_url`` and ``api_key_env`` keys so several OpenAI-compatible providers
(Featherless, AI/ML API, a local server) can run in the same room without
fighting over a single global OPENAI_BASE_URL.

    safety_verifier:
      framework: langgraph
      provider: openai
      model: meta-llama/Meta-Llama-3.1-70B-Instruct
      base_url: https://api.featherless.ai/v1
      api_key_env: FEATHERLESS_API_KEY

Any key beyond framework/model/provider/base_url/api_key_env is forwarded as a
keyword argument to the underlying adapter.

Supported frameworks: anthropic, pydantic_ai, langgraph, claude_sdk, codex,
gemini, google_adk.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger("adapter_factory")

ROOT_DIR = Path(__file__).resolve().parent
BOARD_DIR = ROOT_DIR / "board"

# Frameworks whose adapters accept the portable CustomToolDef tuples returned by
# tools.fda_tools.build_fda_tools. langgraph/pydantic_ai take native formats
# (LangChain tools / bare callables) instead, so live tools are skipped there.
TUPLE_TOOL_FRAMEWORKS = frozenset({"anthropic", "claude_sdk", "gemini", "google_adk"})

_RESERVED = ("framework", "model", "provider", "base_url", "api_key_env", "fallback")


def credentials_path() -> Path:
    return ROOT_DIR / "agent_config.yaml"


def agent_ids_path() -> Path:
    return ROOT_DIR / ".agent_ids.txt"


def load_credentials(agent_key: str) -> tuple[str, str]:
    from thenvoi.config import load_agent_config

    return load_agent_config(agent_key, config_path=credentials_path())


def _infer_provider(model: str) -> str:
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith("gpt") or model.startswith("o"):
        return "openai"
    if model.startswith("gemini"):
        return "google"
    raise ValueError(
        f"Cannot infer provider for model '{model}'. Add an explicit 'provider:' "
        "key in agents.yaml (openai | anthropic | google)."
    )


def _load_config() -> dict:
    with open(BOARD_DIR / "agents.yaml") as f:
        return yaml.safe_load(f)


def resolve_agent_cfg(agent_key: str, config: dict | None = None) -> dict:
    """Return the *effective* config for an agent, applying its fallback.

    An agent may declare an ``api_key_env`` plus a ``fallback:`` block. If the
    required key is not present in the environment, we transparently switch to
    the fallback so the demo always runs — e.g. the cross-provider Safety
    Verifier (Groq) falls back to the local Claude SDK when GROQ_API_KEY is unset.
    """
    config = config or _load_config()
    cfg = dict(config[agent_key])

    key_env = cfg.get("api_key_env")
    if key_env and not os.environ.get(key_env) and cfg.get("fallback"):
        fb = dict(cfg["fallback"])
        logger.warning(
            "%s: %s not set — falling back to %s/%s (set %s for the cross-provider cast).",
            agent_key, key_env, fb.get("framework"), fb.get("model"), key_env,
        )
        cfg = fb
    cfg.pop("fallback", None)
    return cfg


def resolve_framework(agent_key: str) -> str:
    """The effective framework for an agent after fallback resolution."""
    return resolve_agent_cfg(agent_key)["framework"]


def create_adapter(
    agent_key: str,
    custom_section: str,
    *,
    additional_tools: list | None = None,
):
    config = _load_config()
    agent_cfg = resolve_agent_cfg(agent_key, config)
    framework = agent_cfg["framework"]
    model = agent_cfg["model"]
    provider = agent_cfg.get("provider") or _infer_provider(model)
    base_url = agent_cfg.get("base_url")
    api_key = os.environ.get(agent_cfg["api_key_env"]) if agent_cfg.get("api_key_env") else None
    extras = {k: v for k, v in agent_cfg.items() if k not in _RESERVED}

    if framework == "anthropic":
        from thenvoi.adapters import AnthropicAdapter

        return AnthropicAdapter(
            model=model, custom_section=custom_section, additional_tools=additional_tools, **extras
        )

    if framework == "pydantic_ai":
        from thenvoi.adapters import PydanticAIAdapter

        return PydanticAIAdapter(
            model=f"{provider}:{model}",
            custom_section=custom_section,
            additional_tools=additional_tools,
            **extras,
        )

    if framework == "langgraph":
        from langgraph.checkpoint.memory import InMemorySaver
        from thenvoi.adapters import LangGraphAdapter

        if provider == "anthropic":
            from langchain_anthropic import ChatAnthropic

            llm = ChatAnthropic(model=model)
        else:
            # OpenAI or any OpenAI-compatible endpoint (Featherless, AI/ML API,
            # Ollama, vLLM, ...). base_url/api_key fall back to OPENAI_* env when omitted.
            from langchain_openai import ChatOpenAI

            kwargs: dict = {"model": model}
            if base_url:
                kwargs["base_url"] = base_url
            if api_key:
                kwargs["api_key"] = api_key
            # ChatOpenAI-level knobs (streaming, temperature, max_tokens, ...) can be
            # set per-agent in agents.yaml. We pull them out of `extras` so they go to
            # the LLM, not the adapter. streaming=false makes some OpenAI-compatible
            # providers (Featherless) more robust against "No generations found in
            # stream" on long in-room contexts.
            for llm_key in ("streaming", "temperature", "max_tokens", "top_p"):
                if llm_key in extras:
                    kwargs[llm_key] = extras.pop(llm_key)
            llm = ChatOpenAI(**kwargs)

        return LangGraphAdapter(
            llm=llm,
            checkpointer=InMemorySaver(),
            custom_section=custom_section,
            additional_tools=additional_tools,
            **extras,
        )

    if framework == "claude_sdk":
        from thenvoi.adapters import ClaudeSDKAdapter

        return ClaudeSDKAdapter(
            model=model, custom_section=custom_section, additional_tools=additional_tools, **extras
        )

    if framework == "codex":
        from thenvoi.adapters import CodexAdapter, CodexAdapterConfig

        codex_defaults = {"emit_thought_events": True}
        return CodexAdapter(
            config=CodexAdapterConfig(
                model=model, custom_section=custom_section, **{**codex_defaults, **extras}
            ),
            additional_tools=additional_tools,
        )

    if framework == "gemini":
        from thenvoi.adapters import GeminiAdapter

        return GeminiAdapter(
            model=model, custom_section=custom_section, additional_tools=additional_tools, **extras
        )

    if framework == "google_adk":
        from thenvoi.adapters import GoogleADKAdapter

        return GoogleADKAdapter(
            model=model, custom_section=custom_section, additional_tools=additional_tools, **extras
        )

    raise ValueError(f"Unknown framework: {framework}")
