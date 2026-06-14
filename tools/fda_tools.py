"""Wrap the live data functions as Band ``additional_tools``.

These are the portable Thenvoi/Band ``CustomToolDef`` tuples — ``(InputModel,
callable)`` — accepted by the anthropic / claude_sdk / gemini / google_adk
adapters. They let an agent pull *fresh* evidence mid-deliberation (the Verifier
pulling a PubMed interaction paper to challenge the Clinical Reviewer).

Opt-in: agents attach these only when ``USE_LIVE_TOOLS=1`` so the core demo
stays runnable even before the exact tool contract is confirmed on day 1.
The pydantic_ai / langgraph adapters take native formats (bare callables /
LangChain tools); see the Band adapter docs to wire these there.
"""
from __future__ import annotations

import os

from pydantic import BaseModel, Field

from tools.openfda import label_safety, search_recalls
from tools.pubmed import search_pubmed


class LookupRecallsInput(BaseModel):
    drug: str = Field(description="Generic or brand drug name, e.g. 'amiodarone'")


class LookupLabelInput(BaseModel):
    drug: str = Field(description="Generic or brand drug name to fetch the FDA label for")


class SearchPubMedInput(BaseModel):
    query: str = Field(description="PubMed query, e.g. 'amiodarone warfarin interaction bleeding'")


async def _lookup_recalls(drug: str) -> str:
    recalls = search_recalls(drug)
    if not recalls:
        return f"No openFDA enforcement reports found for {drug}."
    return "\n".join(
        f"{r['classification']} ({r['status']}, {r['recall_date']}): {r['reason'][:200]}"
        for r in recalls
    )


async def _lookup_label(drug: str) -> str:
    label = label_safety(drug)
    if not label:
        return f"No FDA label safety sections found for {drug}."
    return "\n".join(f"[{section}] {text}" for section, text in label.items())


async def _search_pubmed(query: str) -> str:
    hits = search_pubmed(query)
    if not hits:
        return f"No PubMed results for: {query}"
    return "\n".join(f"({h['year']}) {h['title']} {h['url']}" for h in hits)


def build_fda_tools() -> list[tuple]:
    """Return the CustomToolDef tuples, or [] when live tools are disabled."""
    if os.environ.get("USE_LIVE_TOOLS") != "1":
        return []
    return [
        (LookupRecallsInput, _lookup_recalls),
        (LookupLabelInput, _lookup_label),
        (SearchPubMedInput, _search_pubmed),
    ]
