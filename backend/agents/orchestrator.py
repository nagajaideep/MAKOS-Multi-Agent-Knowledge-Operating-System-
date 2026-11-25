"""Orchestrator: one question in, one evidence-backed answer out.

Steps for every question:
 1. Understand it locally (vendor name + amount) - no LLM, so a repeat question costs 0 tokens.
 2. Find or join the case for that vendor (duplicate work detection, feature 5).
 3. For each part (registry, debarment, past awards, purchase rule):
      reuse it, refresh it (its data changed) or compute it (first time).
 4. Reuse the stored answer text for this role scope if nothing changed; otherwise ask the
    LLM to write it from the (masked, for officers) evidence.
 5. Record tokens, time, audit trail; propose an action if one is needed.
"""
import re
import time

from backend.agents import data_agent, document_agent, rule_agent
from backend.database import db
from backend.governance import roles
from backend.memory import answer_layer as AL
from backend.services import audit, budget, llm
from backend.workflows import approvals

AMOUNT = re.compile(r"(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*(crore|cr|lakhs?|lacs?|l\b|k\b|thousand)?", re.I)


def parse_amount(text: str):
    best = None
    for m in AMOUNT.finditer(text):
        num, unit = m.group(1).replace(",", ""), (m.group(2) or "").lower()
        if not num or num == ".":
            continue
        v = float(num)
        if unit.startswith(("crore", "cr")):
            v *= 1e7
        elif unit.startswith(("lakh", "lac")) or unit == "l":
            v *= 1e5
        elif unit in ("k", "thousand"):
            v *= 1e3
        elif not (m.group(0).strip().startswith(("₹", "rs", "Rs", "INR", "inr")) or v >= 1000):
            continue
        best = v if best is None else max(best, v)
    return best


class AskError(Exception):
    pass


# ---- cases (feature 5) ----------------------------------------------------------

def open_case(user_id, vendor, purpose="Vendor check"):
    """Return (case_id, joined_info). Joins an open case for the same vendor instead of starting a new one."""
    ts = audit.now()
    with db.state() as s:
        r = s.execute("SELECT * FROM cases WHERE vendor_id=? AND status='open' ORDER BY case_id LIMIT 1", (vendor["vendor_id"],)).fetchone()
        if r:
            cid = r["case_id"]
            member = s.execute("SELECT 1 FROM case_members WHERE case_id=? AND user_id=?", (cid, user_id)).fetchone()
            if member:
                return cid, {"case_id": cid, "already_member": True, "message": f"You are already part of case #{cid} for {vendor['name']}."}
            s.execute("INSERT INTO case_members VALUES(?,?,?,?)", (cid, user_id, ts, "joined (duplicate detected)"))
            starter = roles.USERS[r["created_by"]]["name"]
            return cid, {"case_id": cid, "started_by": starter,
                         "message": f"{starter} already has an open case for {vendor['name']}. You were added to case #{cid} instead of starting a new check."}
        cur = s.execute("INSERT INTO cases(vendor_id, vendor_name, purpose, status, created_by, created_at) VALUES(?,?,?,?,?,?)",
                        (vendor["vendor_id"], vendor["name"], purpose, "open", user_id, ts))
        cid = cur.lastrowid
        s.execute("INSERT INTO case_members VALUES(?,?,?,?)", (cid, user_id, ts, "started"))
    return cid, None


def add_message(case_id, user_id, sender, text, meta=None):
    import json
    with db.state() as s:
        s.execute("INSERT INTO messages(case_id, user_id, sender, text, meta, created_at) VALUES(?,?,?,?,?,?)",
                  (case_id, user_id, sender, text, json.dumps(meta or {}, default=str), audit.now()))


# ---- parts -----------------------------------------------------------------------

def _compute(part_type, vendor, amount, who, chain, case_id):
    """Run the agents for one part. Returns (result, source_keys)."""
    vid = vendor["vendor_id"]
    if part_type == "registry":
        v = data_agent.vendor(vid, on_behalf_of=who, chain=chain + " → Data Agent", case_id=case_id)
        res = rule_agent.check_registry(v, who, chain + " → Rule Agent", case_id)
        res["summary"] = v["mca_status_label"] or "Not in registry"
        res["cin"] = v["cin"]
        res["bank_account"] = v["bank_account"]
        return res, [f"vendor:{vid}"]
    if part_type == "debarment":
        v = data_agent.vendor(vid, on_behalf_of=who, chain=chain + " → Data Agent", case_id=case_id)
        deb = data_agent.debarments_for(vid, v["norm_name"], on_behalf_of=who, chain=chain + " → Data Agent", case_id=case_id)
        res = rule_agent.check_debarment(v, deb, who, chain + " → Rule Agent", case_id)
        return res, [f"debar:{vid}|{v['norm_name']}"]
    if part_type == "history":
        aw = data_agent.awards_for(vid, on_behalf_of=who, chain=chain + " → Data Agent", case_id=case_id)
        return rule_agent.check_history(aw, who, chain + " → Rule Agent", case_id), [f"awards:{vid}"]
    if part_type == "rule":
        return rule_agent.purchase_method(amount, who, chain + " → Rule Agent", case_id), [rule_agent.rules_source_key()]
    raise ValueError(part_type)


def _part_key(part_type, vendor, amount):
    return f"rule|{int(amount)}" if part_type == "rule" else f"{part_type}|v{vendor['vendor_id']}"


LABELS = {"registry": "Company registry (MCA)", "debarment": "Debarment lists", "history": "Past contract awards (CPPP)",
          "rule": "Purchase rule (GFR 2017)"}


def _evidence(parts, scope_name, only=None):
    lines = []
    for p in parts:
        if only is not None and p["part_key"] not in only:
            continue
        r = roles.mask_dict(p["result"], scope_name)
        lines.append(f"[{p['part_type']}] {LABELS[p['part_type']]}: status={r['status']}; {roles.mask_text(r['message'], scope_name)} "
                     f"sources={','.join(r.get('sources', []))}")
        for cid in r.get("sources", []):
            if cid.startswith("GFR-"):
                c = document_agent.clause(cid)
                if c:
                    lines.append(f"  <clause id='{cid}' page='{c['page']}'>{c['text'][:700]}</clause>")
    return "\n".join(lines)


def source_label(src: str) -> str:
    if src.startswith("GFR-"):
        return "GFR Rule " + src[4:]
    if src.startswith("MCA:"):
        return "MCA company master"
    if src.startswith("CPPP:"):
        return "CPPP award record"
    if src.startswith("LIST:"):
        return src[5:]
    return src


def _fallback_text(decision, parts, scope_name):
    out = [f"Decision: {decision['label']}."]
    for p in parts:
        r = roles.mask_dict(p["result"], scope_name)
        labels = list(dict.fromkeys(source_label(x) for x in r.get("sources", [])))
        src = f" (Source: {'; '.join(labels)})" if labels else " (No source found – not confirmed)"
        out.append(f"• {LABELS[p['part_type']]}: {roles.mask_text(r['message'], scope_name)}{src}")
    return "\n".join(out)


# ---- main entry --------------------------------------------------------------------

def ask(user_id: str, question: str) -> dict:
    t0 = time.perf_counter()
    u = roles.user(user_id)
    roles.require(user_id, "ask")
    scope_name = roles.scope(user_id)
    chain = audit.chain_for(u["name"], "Orchestrator")

    vendor = data_agent.find_vendor(question, on_behalf_of=user_id, chain=chain + " → Data Agent")
    amount = parse_amount(question)
    if not vendor:
        audit.log(user_id, chain, "Orchestrator", "ask", {"question": question}, "NO_VENDOR")
        raise AskError("No vendor from the vendor master was found in the question. Please give the vendor's name.")

    case_id, joined = open_case(user_id, vendor)
    if joined and joined.get("already_member"):
        joined = None
    add_message(case_id, user_id, "user", question)
    audit.log(user_id, chain, "Orchestrator", "ask", {"question": question, "vendor": vendor["name"], "amount": amount,
                                                      "scope": scope_name}, "OK", case_id)

    part_types = ["registry", "debarment", "history"] + (["rule"] if amount else [])
    parts, states = [], {}
    for pt in part_types:
        key = _part_key(pt, vendor, amount)
        p = AL.get_part(key)
        if p is None:
            res, src = _compute(pt, vendor, amount, user_id, chain, case_id)
            p = AL.save_part(key, pt, vendor["vendor_id"], {"amount": amount}, res, src, user_id)
            states[key] = "computed"
        else:
            stale = AL.stale_sources(p)
            if stale:
                res, src = _compute(pt, vendor, amount, user_id, chain, case_id)
                p = AL.save_part(key, pt, vendor["vendor_id"], {"amount": amount}, res, src, user_id, refreshed=True)
                states[key] = "refreshed"
                audit.log(user_id, chain, "Orchestrator", "part_refreshed", {"part": key, "changed_sources": stale}, "OK", case_id)
            else:
                states[key] = "reused"
        parts.append(p)

    decision = rule_agent.decide({p["part_type"]: p["result"] for p in parts})
    answer_key = f"vendor_check|v{vendor['vendor_id']}|amt{int(amount) if amount else 'none'}"
    stored = AL.get_answer(answer_key, scope_name)
    current_pv = {p["part_key"]: p["refresh_count"] for p in parts}
    refreshed_keys = [k for k, st in states.items() if st == "refreshed"]
    full_evidence = _evidence(parts, scope_name)
    # What one full run would cost without the shared answer layer (prompt + answer).
    baseline = llm.estimate_tokens(llm.SYSTEM + full_evidence) + llm.estimate_tokens(_fallback_text(decision, parts, scope_name))

    if stored and stored["part_versions"] == current_pv:
        kind, text, t_in, t_out, mode = "reuse", stored["text"], 0, 0, "none"
        AL.hit(answer_key, scope_name)
    else:
        budget.check(u["dept"])
        if stored and refreshed_keys:
            kind = "refresh"
            ev = (f"Previous decision: {stored['decision']}. Only these checks changed:\n"
                  + _evidence(parts, scope_name, only=set(refreshed_keys))
                  + f"\nUnchanged checks: {', '.join(p['part_type'] for p in parts if p['part_key'] not in refreshed_keys)}.")
        else:
            kind = "full_run" if any(st == "computed" for st in states.values()) or not stored else "refresh"
            if not any(st == "computed" for st in states.values()) and not stored:
                kind = "new_scope"   # parts shared, only the wording for this role is new
            ev = full_evidence
        ev = f"<decision>{decision['label']}</decision>\n" + ev
        text, t_in, t_out, mode = llm.compose(ev, _fallback_text(decision, parts, scope_name))
        budget.charge(u["dept"], t_in + t_out)
        AL.save_answer(answer_key, scope_name, "vendor_check", vendor["vendor_id"], amount, parts, text, decision["decision"], user_id)

    changes = AL.seen_changes(user_id, parts)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    budget.record(user_id, u["dept"], kind, question, t_in, t_out, baseline, len(parts), len(refreshed_keys), ms, mode)

    proposal = approvals.propose_for(case_id, vendor, decision, parts, user_id, chain)

    shown_parts = []
    for p in parts:
        r = roles.mask_dict(p["result"], scope_name)
        shown_parts.append({
            "part_key": p["part_key"], "type": p["part_type"], "label": LABELS[p["part_type"]],
            "state": states[p["part_key"]], "status": r["status"], "message": roles.mask_text(r["message"], scope_name),
            "sources": r.get("sources", []), "masked_fields": r.get("_masked", []),
            "details": {k: v for k, v in r.items() if k not in ("message", "sources", "status", "_masked")},
            "computed_for": roles.USERS.get(p["computed_for"], {}).get("name"), "updated_at": p["refreshed_at"],
        })
    result = {
        "case_id": case_id, "joined": joined, "vendor": {"vendor_id": vendor["vendor_id"], "name": vendor["name"]},
        "amount": amount if scope_name == "full" else roles.mask_amount(amount) if amount else None,
        "scope": scope_name, "decision": decision, "answer": text, "parts": shown_parts,
        "kind": kind, "tokens": {"in": t_in, "out": t_out, "total": t_in + t_out, "mode": mode,
                                 "without_makos": baseline},
        "parts_refreshed": len(refreshed_keys), "parts_total": len(parts), "changes_since_last_time": changes,
        "ms": ms, "chain": chain, "proposed_action": proposal, "rules_version": rule_agent.rules_version(),
    }
    add_message(case_id, user_id, "makos", text, {"kind": kind, "decision": decision["decision"], "scope": scope_name,
                                                  "tokens": t_in + t_out, "changes": changes})
    audit.log(user_id, chain, "Orchestrator", "answer", {"kind": kind, "decision": decision["decision"], "tokens": t_in + t_out,
                                                         "parts": states}, "OK", case_id)
    return result
