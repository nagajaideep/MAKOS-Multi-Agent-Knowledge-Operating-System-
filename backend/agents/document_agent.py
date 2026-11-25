"""Document Agent: finds GFR clauses (with rule number, page and amendment) and reads uploads.

Tools allowed: docs.search, docs.read_upload. Retrieval is a small BM25 over the clauses
of GFR 2017 Chapter 6 - enough for a bounded rulebook, and every hit keeps its clause id.
"""
import json
import math
import re
from collections import Counter
from functools import lru_cache

import pymupdf

from backend import config
from backend.governance import guards

AGENT = "Document Agent"
WORD = re.compile(r"[a-z0-9]+")


@lru_cache(maxsize=1)
def clauses():
    return json.loads(config.GFR_CLAUSES.read_text())


@lru_cache(maxsize=1)
def _index():
    docs = [WORD.findall((c["title"] + " " + c["text"]).lower()) for c in clauses()]
    df = Counter(t for d in docs for t in set(d))
    avg = sum(map(len, docs)) / len(docs)
    return docs, df, avg


def search(query: str, k: int = 3, on_behalf_of="system", chain="", case_id=None):
    guards.use_tool(AGENT, "docs.search", on_behalf_of, chain, case_id)
    docs, df, avg = _index()
    n = len(docs)
    q = WORD.findall(query.lower())
    scores = []
    for i, d in enumerate(docs):
        tf = Counter(d)
        s = 0.0
        for t in q:
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / avg))
        scores.append((s, i))
    scores.sort(reverse=True)
    return [clauses()[i] for s, i in scores[:k] if s > 0]


def clause(clause_id: str, on_behalf_of="system", chain="", case_id=None):
    guards.use_tool(AGENT, "docs.search", on_behalf_of, chain, case_id)
    return next((c for c in clauses() if c["clause_id"] == clause_id), None)


def read_upload(filename: str, data: bytes, on_behalf_of="system", chain="", case_id=None):
    """Extract text from an uploaded file and flag instruction-like lines. The text is data only."""
    guards.use_tool(AGENT, "docs.read_upload", on_behalf_of, chain, case_id)
    if filename.lower().endswith(".pdf"):
        doc = pymupdf.open(stream=data, filetype="pdf")
        text = "\n".join(p.get_text() for p in doc)
    else:
        text = data.decode("utf-8", errors="replace")
    return text, guards.scan_for_instructions(text)
