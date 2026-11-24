"""Shared answer layer (features 1-4).

* An answer is made of PARTS. Each part is stored once and shared by everyone.
* Each part remembers the SOURCES it used and their VERSIONS (a vendor row, the
  debarment rows for that vendor, the vendor's award rows, the GFR rule version).
* There is no timer. When a question comes again, `current_version()` is compared with the
  stored version for every source. This is plain SQL - no LLM, no tokens.
* Only parts whose sources changed are recomputed (part-by-part refresh), and the old and
  new summaries are compared to tell the user what changed since they last asked.
"""
import json

from backend.database import db
from backend.services import audit


# ---- source versions ----------------------------------------------------------

def current_version(source_key: str) -> str:
    kind, _, ident = source_key.partition(":")
    with db.work(readonly=True) as w:
        if kind == "vendor":
            r = w.execute("SELECT row_version FROM vendors WHERE vendor_id=?", (ident,)).fetchone()
            return str(r[0]) if r else "missing"
        if kind == "debar":
            vid, _, norm = ident.partition("|")
            r = w.execute("SELECT COUNT(*), COALESCE(SUM(row_version),0) FROM debarments WHERE vendor_id=? OR norm_name=?",
                          (vid, norm)).fetchone()
            return f"{r[0]}.{r[1]}"
        if kind == "awards":
            r = w.execute("SELECT COUNT(*), COALESCE(SUM(row_version),0) FROM awards WHERE vendor_id=?", (ident,)).fetchone()
            return f"{r[0]}.{r[1]}"
    if kind == "rules":
        return db.get_setting("gfr_version", "after") + "." + db.get_setting("gfr_revision", "1")
    raise KeyError(source_key)


def versions_for(source_keys):
    return {k: current_version(k) for k in source_keys}


# ---- parts --------------------------------------------------------------------

def get_part(part_key):
    with db.state() as s:
        r = s.execute("SELECT * FROM answer_parts WHERE part_key=?", (part_key,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["result"] = json.loads(d["result"])
    d["sources"] = json.loads(d["sources"])
    return d


def stale_sources(part) -> list:
    """Return the sources whose current version differs from the stored one."""
    return [k for k, v in part["sources"].items() if current_version(k) != v]


def save_part(part_key, part_type, vendor_id, params, result, source_keys, computed_for, refreshed=False):
    ts = audit.now()
    with db.state() as s:
        if refreshed:
            s.execute("""UPDATE answer_parts SET result=?, sources=?, refreshed_at=?, computed_for=?,
                         refresh_count=refresh_count+1 WHERE part_key=?""",
                      (json.dumps(result, default=str), json.dumps(versions_for(source_keys)), ts, computed_for, part_key))
        else:
            s.execute("""INSERT OR REPLACE INTO answer_parts(part_key, part_type, vendor_id, params, result, sources,
                         created_at, refreshed_at, computed_for) VALUES(?,?,?,?,?,?,?,?,?)""",
                      (part_key, part_type, vendor_id, json.dumps(params), json.dumps(result, default=str),
                       json.dumps(versions_for(source_keys)), ts, ts, computed_for))
    return get_part(part_key)


def summary(part_type, result) -> str:
    """One short value per part, used to say what changed."""
    if part_type == "registry":
        return result.get("summary") or result["status"]
    if part_type == "debarment":
        return {"ok": "Not on any list", "fail": "Debarred", "cannot_confirm": "Possible name match"}[result["status"]]
    if part_type == "history":
        return f"{result.get('count', 0)} awards, {result.get('single_bid', 0)} single-bid"
    if part_type == "rule":
        return result["message"].split("(")[0].strip()
    return result.get("status", "")


# ---- answers per scope ----------------------------------------------------------

def get_answer(answer_key, scope):
    with db.state() as s:
        r = s.execute("SELECT * FROM answers WHERE answer_key=? AND scope=?", (answer_key, scope)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["part_keys"] = json.loads(d["part_keys"])
    d["part_versions"] = json.loads(d["part_versions"])
    return d


def save_answer(answer_key, scope, intent, vendor_id, amount, parts, text, decision, user_id):
    ts = audit.now()
    pv = {p["part_key"]: p["refresh_count"] for p in parts}
    with db.state() as s:
        s.execute("""INSERT INTO answers(answer_key, scope, intent, vendor_id, amount, part_keys, part_versions, text,
                     decision, created_by, created_at, updated_at, hits) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)
                     ON CONFLICT(answer_key, scope) DO UPDATE SET part_versions=excluded.part_versions,
                     text=excluded.text, decision=excluded.decision, updated_at=excluded.updated_at""",
                  (answer_key, scope, intent, vendor_id, amount, json.dumps([p["part_key"] for p in parts]),
                   json.dumps(pv), text, decision, user_id, ts, ts))


def hit(answer_key, scope):
    with db.state() as s:
        s.execute("UPDATE answers SET hits = hits + 1 WHERE answer_key=? AND scope=?", (answer_key, scope))


# ---- what changed since this user last asked (feature 4) ------------------------------

def seen_changes(user_id, parts) -> list:
    """Compare each part's summary with what this user saw last time, then remember the new one."""
    changes = []
    with db.state() as s:
        s.execute("CREATE TABLE IF NOT EXISTS seen (user_id TEXT, part_key TEXT, summary TEXT, seen_at TEXT, PRIMARY KEY(user_id, part_key))")
        for p in parts:
            new = summary(p["part_type"], p["result"])
            r = s.execute("SELECT summary, seen_at FROM seen WHERE user_id=? AND part_key=?", (user_id, p["part_key"])).fetchone()
            if r and r["summary"] != new:
                changes.append({"part": p["part_type"], "before": r["summary"], "after": new, "since": r["seen_at"]})
            s.execute("INSERT OR REPLACE INTO seen VALUES(?,?,?,?)", (user_id, p["part_key"], new, audit.now()))
    return changes
