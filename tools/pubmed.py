"""Live PubMed search via NCBI E-utilities (free, no API key required).

The Safety Verifier uses this to pull independent literature when challenging
the Clinical Reviewer's assessment — e.g. evidence of a drug-drug interaction
the lead reviewer did not cite.

Docs: https://www.ncbi.nlm.nih.gov/books/NBK25501/
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger("tools.pubmed")

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_TIMEOUT = 15


def _get(path: str, params: dict[str, str]) -> dict[str, Any]:
    url = f"{_EUTILS}/{path}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "second-opinion/0.1"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.info("PubMed request failed (%s): %s", url, exc)
        return {}


def search_pubmed(query: str, retmax: int = 3) -> list[dict[str, str]]:
    """Return up to ``retmax`` recent PubMed hits as {pmid, title, url, year}."""
    search = _get(
        "esearch.fcgi",
        {"db": "pubmed", "term": query, "retmode": "json", "retmax": str(retmax), "sort": "date"},
    )
    ids = (search.get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return []

    summary = _get(
        "esummary.fcgi",
        {"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
    )
    result = summary.get("result") or {}
    out: list[dict[str, str]] = []
    for pmid in ids:
        item = result.get(pmid) or {}
        out.append(
            {
                "pmid": pmid,
                "title": (item.get("title") or "").strip(),
                "year": (item.get("pubdate") or "").split(" ")[0],
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            }
        )
    return out


if __name__ == "__main__":  # python -m tools.pubmed "warfarin amiodarone interaction"
    import sys

    q = " ".join(sys.argv[1:]) or "warfarin amiodarone interaction bleeding"
    for hit in search_pubmed(q):
        print(f"- ({hit['year']}) {hit['title']}  {hit['url']}")
