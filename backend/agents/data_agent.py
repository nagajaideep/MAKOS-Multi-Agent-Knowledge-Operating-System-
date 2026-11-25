"""Data Agent: read-only SQL over the approved tables (vendors, awards, debarments).

Tool allowed: sql.read. Every statement passes the SQL guard first, runs on a read-only
connection, and returns its rows together with the row versions used, so the shared
answer layer can later tell whether those rows changed.
"""
import difflib
import re

from backend.database import db
from backend.governance import guards

AGENT = "Data Agent"


def run(sql: str, params=(), on_behalf_of="system", chain="", case_id=None):
    guards.use_tool(AGENT, "sql.read", on_behalf_of, chain, case_id)
    safe = guards.check_sql(sql)
    with db.work(readonly=True) as w:
        return [dict(r) for r in w.execute(safe, params)]


def _norm(name: str) -> str:
    s = name.upper()
    s = re.sub(r"^\s*(M\s*/\s*S\.?|MESSRS\.?)\s+", "", s)
    s = re.sub(r"\bPRIVATE\b", "PVT", s)
    s = re.sub(r"\bLIMITED\b", "LTD", s)
    s = re.sub(r"[^A-Z0-9& ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_names = None


def find_vendor(text: str, on_behalf_of="system", chain=""):
    """Match a vendor mentioned in free text against the vendor master (local, no LLM)."""
    global _names
    if _names is None:
        _names = run("SELECT vendor_id, name, norm_name FROM vendors LIMIT 100000", on_behalf_of=on_behalf_of, chain=chain)
    t = _norm(text)
    best, best_score = None, 0.0
    for v in _names:
        n = v["norm_name"]
        if not n or len(n) < 4:
            continue
        if f" {n} " in f" {t} ":
            score = 1.0 + len(n) / 100
        else:
            first = " ".join(n.split()[:2])
            if len(first) < 6 or first not in t:
                continue
            score = difflib.SequenceMatcher(None, n, t[t.find(first): t.find(first) + len(n) + 5]).ratio()
        if score > best_score:
            best, best_score = v, score
    return best if best_score >= 0.75 else None


def vendor(vendor_id: int, **kw):
    rows = run("SELECT * FROM vendors WHERE vendor_id = ?", (vendor_id,), **kw)
    return rows[0] if rows else None


def debarments_for(vendor_id: int, norm_name: str, **kw):
    return run("SELECT * FROM debarments WHERE vendor_id = ? OR norm_name = ?", (vendor_id, norm_name), **kw)


def awards_for(vendor_id: int, **kw):
    return run("""SELECT award_id, org_name, title, contract_value, bids_received, contract_date, source_url, row_version
                  FROM awards WHERE vendor_id = ? ORDER BY contract_value DESC""", (vendor_id,), **kw)
