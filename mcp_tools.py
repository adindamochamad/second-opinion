"""
Turn an external MCP server's tools into Thenvoi ``CustomToolDef`` tuples.

Use with any adapter that accepts ``additional_tools`` in the portable tuple
format: ``codex``, ``claude_sdk``, ``anthropic``, ``gemini``, ``google_adk``.

Example
-------

Attach a Harvey MCP server to one of your agents::

    # scenarios/patent_licensing/bg_regulatory_advisor.py
    import asyncio
    from mcp_tools import MCPToolsProvider

    async def main() -> None:
        load_dotenv()
        remove_tools("thenvoi_add_participant", ...)

        scenario = os.path.basename(os.path.dirname(__file__))

        provider = MCPToolsProvider.http(
            url="https://mcp.harvey.ai/mcp",
            headers={"Authorization": f"Bearer {os.environ['HARVEY_OAUTH_TOKEN']}"},
        )
        mcp_tools = await provider.start()

        agent_id, api_key = load_credentials("bg_regulatory_advisor", scenario)
        adapter = create_adapter(
            "bg_regulatory_advisor",
            CUSTOM_SECTION,
            scenario,
            additional_tools=mcp_tools,
        )
        try:
            agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key, ...)
            await agent.run()
        finally:
            await provider.stop()

The provider opens the MCP session on ``start()``, walks the server's tool
list via ``list_tools``, generates a Pydantic input model for each tool from
its JSON schema, and wraps each one in a callable that dispatches back to
``session.call_tool``. The returned list slots straight into the Thenvoi
SDK's ``additional_tools=`` parameter with no further glue.
"""
from __future__ import annotations

import logging
import re
from contextlib import AsyncExitStack
from typing import Any, Callable, Literal

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel, Field, create_model

logger = logging.getLogger(__name__)

CustomToolDef = tuple[type[BaseModel], Callable[..., Any]]
Transport = Literal["stdio", "http"]


class MCPToolsProvider:
    """Long-lived MCP client session exposed as a list of ``CustomToolDef``.

    Factory methods:

    - ``MCPToolsProvider.stdio(command, args)``
        Launch an MCP server as a subprocess over stdio. Works with any Python,
        Node, or binary MCP server, for example::

            MCPToolsProvider.stdio(command="uvx", args=["mcp-server-time"])

    - ``MCPToolsProvider.http(url, headers)``
        Talk to a streamable-HTTP MCP server. This is the transport Harvey's
        public MCP uses. Pass OAuth / bearer tokens via ``headers``::

            MCPToolsProvider.http(
                url="https://mcp.harvey.ai/mcp",
                headers={"Authorization": f"Bearer {token}"},
            )

    Lifecycle:

        provider = MCPToolsProvider.http(url=...)
        tools = await provider.start()           # open session, list tools
        adapter = create_adapter(..., additional_tools=tools)
        try:
            await agent.run()
        finally:
            await provider.stop()                # close session cleanly
    """

    def __init__(
        self,
        *,
        transport: Transport,
        stdio_params: StdioServerParameters | None = None,
        http_url: str | None = None,
        http_headers: dict[str, str] | None = None,
    ) -> None:
        self._transport: Transport = transport
        self._stdio_params = stdio_params
        self._http_url = http_url
        self._http_headers = http_headers
        self._exit_stack = AsyncExitStack()
        self._session: ClientSession | None = None

    @classmethod
    def stdio(
        cls,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> "MCPToolsProvider":
        return cls(
            transport="stdio",
            stdio_params=StdioServerParameters(
                command=command,
                args=args or [],
                env=env,
            ),
        )

    @classmethod
    def http(
        cls,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> "MCPToolsProvider":
        return cls(transport="http", http_url=url, http_headers=headers)

    async def start(self) -> list[CustomToolDef]:
        """Open the MCP session and return the server's tool list as
        ``CustomToolDef`` tuples ready to pass into ``additional_tools=``."""
        if self._transport == "stdio":
            if self._stdio_params is None:
                raise ValueError("stdio transport requires stdio_params")
            read, write = await self._exit_stack.enter_async_context(
                stdio_client(self._stdio_params)
            )
        else:
            if self._http_url is None:
                raise ValueError("http transport requires http_url")
            result = await self._exit_stack.enter_async_context(
                streamablehttp_client(
                    self._http_url,
                    headers=self._http_headers,
                )
            )
            # streamable_http yields (read, write, get_session_id_callback)
            read, write, *_ = result

        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await self._session.initialize()

        tool_list = (await self._session.list_tools()).tools
        logger.info(
            "MCP session (%s) ready: %d tool(s) available — %s",
            self._transport,
            len(tool_list),
            ", ".join(t.name for t in tool_list),
        )
        return [self._wrap_tool(tool) for tool in tool_list]

    async def stop(self) -> None:
        """Close the session and release the underlying transport."""
        await self._exit_stack.aclose()
        self._session = None

    async def __aenter__(self) -> list[CustomToolDef]:
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    # ── internals ────────────────────────────────────────────────────────

    def _wrap_tool(self, tool) -> CustomToolDef:
        """Convert one MCP tool into a ``(PydanticInputModel, callable)`` tuple
        that Thenvoi's ``execute_custom_tool`` helper can dispatch on."""
        mcp_name = tool.name
        description = tool.description or ""
        schema = tool.inputSchema or {"type": "object", "properties": {}}

        model_cls = _json_schema_to_pydantic_model(
            model_name=_safe_pascal(mcp_name) + "Input",
            schema=schema,
            description=description,
        )

        async def _call(inp: BaseModel) -> Any:
            if self._session is None:
                raise RuntimeError(
                    "MCPToolsProvider is not started. Call await provider.start() "
                    "before the tool is invoked."
                )
            arguments = inp.model_dump(exclude_none=True)
            result = await self._session.call_tool(mcp_name, arguments=arguments)
            return _flatten_call_tool_result(result)

        _call.__name__ = f"mcp_{mcp_name}"
        _call.__doc__ = description
        return (model_cls, _call)


# ── schema → Pydantic model ──────────────────────────────────────────────


_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _json_schema_to_pydantic_model(
    *,
    model_name: str,
    schema: dict[str, Any],
    description: str,
) -> type[BaseModel]:
    """Build a Pydantic model from a JSON schema so Thenvoi's CustomToolDef
    helper can introspect it the same way it introspects hand-written models.
    Only the top-level properties are lifted; nested objects fall back to
    ``dict`` rather than being recursively synthesised."""
    properties: dict[str, Any] = schema.get("properties", {}) or {}
    required_set: set[str] = set(schema.get("required", []) or [])

    fields: dict[str, tuple[Any, Any]] = {}
    for prop_name, prop_schema in properties.items():
        py_type = _JSON_TYPE_MAP.get(prop_schema.get("type", "string"), str)
        prop_description = prop_schema.get("description", "") or ""
        if prop_name in required_set:
            fields[prop_name] = (py_type, Field(..., description=prop_description))
        else:
            fields[prop_name] = (
                py_type | None,
                Field(default=None, description=prop_description),
            )

    if not fields:
        # create_model complains about empty field dicts; synthesise a no-op.
        fields = {"__placeholder": (str | None, Field(default=None))}

    model: type[BaseModel] = create_model(model_name, __base__=BaseModel, **fields)
    model.__doc__ = description
    return model


def _safe_pascal(name: str) -> str:
    """Convert an MCP tool name (which may contain hyphens, underscores,
    dots, or slashes) into a safe PascalCase Python identifier."""
    cleaned = re.sub(r"[^0-9A-Za-z]+", "_", name)
    parts = [p for p in cleaned.split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts) or "Tool"


def _flatten_call_tool_result(result) -> str:
    """Convert an MCP ``CallToolResult`` into a plain string the LLM can
    read back. Text blocks are concatenated; non-text content is stringified."""
    if getattr(result, "isError", False):
        error_text = _content_as_text(result.content)
        return f"[MCP tool error] {error_text}"
    return _content_as_text(result.content)


def _content_as_text(content) -> str:
    if not content:
        return "(no content)"
    pieces: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text is not None:
            pieces.append(text)
        else:
            pieces.append(str(item))
    return "\n".join(pieces)
