"""Live openFDA access (free, no API key required).

openFDA exposes the same drug-safety data the FDA publishes: enforcement
reports (recalls), structured product labeling (boxed warnings, interactions,
contraindications) and adverse-event reports. We read it live at runtime so the
review board reasons over *current* regulatory facts, not a frozen database.

Docs: https://open.fda.gov/apis/drug/
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger("tools.openfda")

_BASE = "https://api.fda.gov"
_TIMEOUT = 15  # seconds; openFDA can be slow under load


def _get(path: str, params: dict[str, str]) -> dict[str, Any]:
    """GET an openFDA endpoint. Returns {} on any failure (never raises)."""
    url = f"{_BASE}{path}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "second-opinion/0.1"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # network, 404 (no results), rate limit, etc.
        logger.info("openFDA request failed (%s): %s", url, exc)
        return {}


def _drug_query(term: str, field: str) -> str:
    """Quote a drug term for a given openFDA field."""
    return f'{field}:"{term}"'


def search_recalls(drug: str, limit: int = 3) -> list[dict[str, str]]:
    """Recent enforcement reports (recalls) mentioning a drug.

    Returns a list of {classification, status, reason, product, recall_date,
    distribution}. Empty list if none / on failure.
    """
    for field in ("openfda.generic_name", "openfda.brand_name", "product_description"):
        data = _get(
            "/drug/enforcement.json",
            {"search": _drug_query(drug, field), "limit": str(limit), "sort": "recall_initiation_date:desc"},
        )
        results = data.get("results")
        if results:
            return [
                {
                    "classification": r.get("classification", "Unknown"),
                    "status": r.get("status", "Unknown"),
                    "reason": r.get("reason_for_recall", "").strip(),
                    "product": r.get("product_description", "").strip()[:300],
                    "recall_date": r.get("recall_initiation_date", "Unknown"),
                    "distribution": r.get("distribution_pattern", "").strip()[:200],
                }
                for r in results
            ]
    return []


def label_safety(drug: str) -> dict[str, str]:
    """Boxed warning, contraindications and interactions from the live label.

    Returns {boxed_warning, contraindications, drug_interactions, warnings}.
    Values are truncated; missing sections are omitted.
    """
    for field in ("openfda.generic_name", "openfda.brand_name"):
        data = _get("/drug/label.json", {"search": _drug_query(drug, field), "limit": "1"})
        results = data.get("results")
        if results:
            r = results[0]
            out: dict[str, str] = {}
            for key, target in (
                ("boxed_warning", "boxed_warning"),
                ("contraindications", "contraindications"),
                ("drug_interactions", "drug_interactions"),
                ("warnings_and_cautions", "warnings"),
                ("warnings", "warnings"),
            ):
                val = r.get(key)
                if val and target not in out:
                    text = " ".join(val) if isinstance(val, list) else str(val)
                    out[target] = text.strip()[:600]
            return out
    return {}


if __name__ == "__main__":  # quick manual check: python -m tools.openfda warfarin
    import sys

    drug = sys.argv[1] if len(sys.argv) > 1 else "warfarin"
    print(f"Recalls for {drug}:")
    for rec in search_recalls(drug):
        print(" -", rec["classification"], "|", rec["reason"][:120])
    print(f"\nLabel safety for {drug}:")
    for k, v in label_safety(drug).items():
        print(f" [{k}] {v[:160]}")
