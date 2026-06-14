"""Live regulated-data tools shared by the Second Opinion agents.

Pure functions with no Band/LLM dependency so they are unit-testable on their
own and can be wired into any adapter (as portable CustomToolDef tuples for the
Anthropic/Claude/Gemini adapters, or as plain callables/LangChain tools).
"""
