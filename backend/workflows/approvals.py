"""Approval only for risky actions (feature 13).

Low-risk follow-ups (adding a review note to the case) run automatically.
Risky ones (putting a vendor on hold in the vendor portal) wait for the manager. If the
manager does not decide within APPROVAL_TIMEOUT_MIN minutes, the request escalates to the
vigilance officer. Only an approved request reaches the Action Agent.
"""
import json
from datetime import datetime, timedelta

from backend import config
from backend.database import db
from backend.governance import roles
from backend.services import audit

RISK = {"hold_vendor": "high", "add_review_note": "low"}


def _manager_of(user_id):
    return roles.USERS[user_id]["manager"] or "rao"


def propose_for(case_id, vendor, decision, parts, user_id, chain):
    if decision["decision"] == "DO_NOT_PLACE_ORDER":
        reason = "; ".join(p["result"]["message"] for p in parts if p["result"]["status"] == "fail")
        return request(case_id, "hold_vendor", {"vendor_id": vendor["vendor_id"], "vendor_name": vendor["name"]},
                       reason, user_id, chain)
    if decision["decision"] == "NEEDS_REVIEW":
        note = "; ".join(p["result"]["message"] for p in parts if p["result"]["status"] == "cannot_confirm")
        with db.state() as s:
            exists = s.execute("SELECT 1 FROM messages WHERE case_id=? AND sender='system' AND text LIKE 'Review note%'",
                               (case_id,)).fetchone()
            if not exists:
                s.execute("INSERT INTO messages(case_id, user_id, sender, text, meta, created_at) VALUES(?,?,?,?,?,?)",
                          (case_id, user_id, "system", f"Review note added automatically (low risk): {note}", "{}", audit.now()))
        audit.log(user_id, chain + " → Orchestrator", "Orchestrator", "add_review_note", {"note": note[:300]}, "AUTO (low risk)", case_id)
        return {"action": "add_review_note", "risk": "low", "status": "done automatically", "note": note}
    return None


def request(case_id, action, params, reason, user_id, chain):
    with db.state() as s:
        r = s.execute("""SELECT * FROM approvals WHERE action=? AND json_extract(params,'$.vendor_id')=?
                         AND status IN ('pending','escalated','approved','done') ORDER BY approval_id DESC LIMIT 1""",
                      (action, params["vendor_id"])).fetchone()
        if r:
            return _public(dict(r))
        now = datetime.now()
        cur = s.execute("""INSERT INTO approvals(case_id, action, params, risk, reason, requested_for, status, assigned_to,
                           created_at, due_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (case_id, action, json.dumps(params), RISK[action], reason, user_id, "pending", _manager_of(user_id),
                         now.isoformat(timespec="seconds"),
                         (now + timedelta(minutes=config.APPROVAL_TIMEOUT_MIN)).isoformat(timespec="seconds")))
        aid = cur.lastrowid
    audit.log(user_id, chain, "Orchestrator", f"approval_requested:{action}", {"approval_id": aid, "risk": RISK[action]},
              "WAITING", case_id)
    return get(aid)


def _public(a):
    a = dict(a)
    a["params"] = json.loads(a["params"]) if isinstance(a["params"], str) else a["params"]
    a["assigned_to_name"] = roles.USERS.get(a["assigned_to"], {}).get("name")
    a["requested_for_name"] = roles.USERS.get(a["requested_for"], {}).get("name")
    a["decided_by_name"] = roles.USERS.get(a.get("decided_by"), {}).get("name")
    with db.state() as s:
        r = s.execute("SELECT action_id, verified FROM actions WHERE approval_id=? ORDER BY action_id DESC LIMIT 1",
                      (a["approval_id"],)).fetchone()
    a["action_id"], a["action_verified"] = (r["action_id"], bool(r["verified"])) if r else (None, None)
    return a


def get(aid):
    with db.state() as s:
        r = s.execute("SELECT * FROM approvals WHERE approval_id=?", (aid,)).fetchone()
    return _public(dict(r)) if r else None


def escalate_overdue():
    now = datetime.now().isoformat(timespec="seconds")
    with db.state() as s:
        rows = s.execute("SELECT * FROM approvals WHERE status='pending' AND due_at < ?", (now,)).fetchall()
        for r in rows:
            s.execute("UPDATE approvals SET status='escalated', assigned_to='priya' WHERE approval_id=?", (r["approval_id"],))
    for r in rows:
        audit.log(r["requested_for"], "System → Approval Gate", "Approval Gate", "escalated",
                  {"approval_id": r["approval_id"], "after_minutes": config.APPROVAL_TIMEOUT_MIN}, "ESCALATED", r["case_id"])


def list_for(user_id):
    escalate_overdue()
    u = roles.user(user_id)
    with db.state() as s:
        if u["role"] in ("auditor", "admin"):
            rows = s.execute("SELECT * FROM approvals ORDER BY approval_id DESC").fetchall()
        elif u["role"] == "manager":
            rows = s.execute("SELECT * FROM approvals WHERE assigned_to=? OR requested_for IN (%s) ORDER BY approval_id DESC"
                             % ",".join("?" * len(roles.team_of(user_id))), (user_id, *roles.team_of(user_id))).fetchall()
        else:
            rows = s.execute("SELECT * FROM approvals WHERE requested_for=? ORDER BY approval_id DESC", (user_id,)).fetchall()
    return [_public(dict(r)) for r in rows]


def decide(aid, user_id, approve: bool, note: str = ""):
    escalate_overdue()
    a = get(aid)
    if not a:
        raise KeyError(aid)
    u = roles.user(user_id)
    allowed = (a["status"] == "pending" and roles.can(user_id, "approve") and a["assigned_to"] == user_id) or \
              (a["status"] == "escalated" and roles.can(user_id, "decide_escalated"))
    chain = audit.chain_for(u["name"], "Approval Gate")
    if not allowed:
        audit.log(user_id, chain, "Approval Gate", f"decide:{a['action']}", {"approval_id": aid, "status": a["status"]}, "DENIED", a["case_id"])
        raise roles.Forbidden(f"{u['name']} cannot decide this request (status {a['status']}, assigned to {a['assigned_to_name']})")
    status = "approved" if approve else "rejected"
    with db.state() as s:
        s.execute("UPDATE approvals SET status=?, decided_by=?, decided_at=?, decision_note=? WHERE approval_id=?",
                  (status, user_id, audit.now(), note, aid))
    audit.log(user_id, chain, "Approval Gate", f"{status}:{a['action']}", {"approval_id": aid, "note": note}, status.upper(), a["case_id"])
    result = {"approval": get(aid)}
    if approve and a["action"] == "hold_vendor":
        from backend.workflows import browser_agent
        result["action"] = browser_agent.hold_vendor(aid, a["params"]["vendor_id"], a["params"]["vendor_name"],
                                                     reason=a["reason"], on_behalf_of=a["requested_for"],
                                                     approved_by=user_id, case_id=a["case_id"])
        with db.state() as s:
            s.execute("UPDATE approvals SET status=? WHERE approval_id=?",
                      ("done" if result["action"]["verified"] else "escalated", aid))
        result["approval"] = get(aid)
    return result
