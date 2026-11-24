"""MAKOS API server.

Run:  uvicorn backend.main:app --port 8000
Every request names the acting user (`user` field or query parameter); the governance
layer decides what that role may do and see.
"""
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import config
from backend.agents import data_agent, document_agent, orchestrator, rule_agent
from backend.database import db
from backend.governance import guards, roles
from backend.services import audit, budget, demo, llm
from backend.workflows import approvals, browser_agent, portal

@asynccontextmanager
async def lifespan(_app):
    fresh = not config.STATE_DB.exists()
    db.init()
    if fresh:
        demo.reset()
    yield


app = FastAPI(title="MAKOS – Ask Once, Answer Everyone", lifespan=lifespan)
app.include_router(portal.router)


def _err(e: Exception):
    if isinstance(e, (roles.Forbidden, guards.ToolBlocked)):
        raise HTTPException(403, str(e))
    if isinstance(e, budget.BudgetExceeded):
        raise HTTPException(429, str(e))
    if isinstance(e, (orchestrator.AskError, guards.UnsafeSQL)):
        raise HTTPException(400, str(e))
    raise e


class Ask(BaseModel):
    user: str
    question: str


class Decide(BaseModel):
    user: str
    approve: bool
    note: str = ""


class StartCase(BaseModel):
    user: str
    vendor: str


@app.get("/api/users")
def users():
    return [{"id": k, **v, "scope": roles.scope(k)} for k, v in roles.USERS.items()]


@app.get("/api/status")
def status():
    return {"llm_mode": llm.mode(), "model": config.LLM_MODEL, "rules_version": rule_agent.rules_version(),
            "demo_vendors": demo.DEMO_VENDORS}


@app.post("/api/ask")
def ask(body: Ask):
    try:
        return orchestrator.ask(body.user, body.question)
    except Exception as e:
        _err(e)


@app.post("/api/cases/start")
def start_case(body: StartCase):
    try:
        roles.require(body.user, "start_case")
        v = data_agent.find_vendor(body.vendor, on_behalf_of=body.user, chain=roles.USERS[body.user]["name"])
        if not v:
            raise orchestrator.AskError("vendor not found")
        cid, joined = orchestrator.open_case(body.user, v)
        audit.log(body.user, audit.chain_for(roles.USERS[body.user]["name"], "Orchestrator"), "Orchestrator",
                  "start_case", {"vendor": v["name"], "joined": bool(joined)},
                  "ALREADY MEMBER" if joined and joined.get("already_member") else "JOINED EXISTING" if joined else "OK", cid)
        return {"case_id": cid, "joined": joined, "vendor": v["name"]}
    except Exception as e:
        _err(e)


def _members(s, cid):
    return {r["user_id"] for r in s.execute("SELECT user_id FROM case_members WHERE case_id=?", (cid,))}


@app.get("/api/cases")
def cases(user: str):
    try:
        roles.user(user)
        out, total = [], 0
        with db.state() as s:
            for c in s.execute("SELECT * FROM cases ORDER BY case_id DESC"):
                total += 1
                m = _members(s, c["case_id"])
                if roles.can_view_case(user, m):
                    out.append({**dict(c), "created_by_name": roles.USERS[c["created_by"]]["name"],
                                "members": [roles.USERS[x]["name"] for x in sorted(m)]})
        return {"cases": out, "visible": len(out), "total": total}
    except Exception as e:
        _err(e)


@app.get("/api/cases/{case_id}")
def case(case_id: int, user: str):
    try:
        with db.state() as s:
            c = s.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
            if not c:
                raise HTTPException(404, "no such case")
            m = _members(s, case_id)
            if not roles.can_view_case(user, m):
                audit.log(user, roles.USERS[user]["name"], "Governance", "view_case", {"case_id": case_id}, "DENIED", case_id)
                raise roles.Forbidden(f"{roles.USERS[user]['name']} may not view case #{case_id}")
            members = [dict(r) for r in s.execute("SELECT * FROM case_members WHERE case_id=?", (case_id,))]
            msgs = [dict(r) for r in s.execute("SELECT * FROM messages WHERE case_id=? ORDER BY msg_id", (case_id,))]
        scope = roles.scope(user)
        for msg in msgs:
            msg["meta"] = json.loads(msg["meta"] or "{}")
            msg["user_name"] = roles.USERS.get(msg["user_id"], {}).get("name")
            # A stored MAKOS answer was written for one scope; officers never see a full-scope text.
            if msg["sender"] == "makos" and msg["meta"].get("scope") == "full" and scope != "full":
                msg["text"] = "(Answer written for a manager - hidden for your role.)"
            msg["text"] = roles.mask_text(msg["text"], scope)
        return {"case": dict(c), "members": [{**x, "name": roles.USERS[x["user_id"]]["name"]} for x in members],
                "messages": msgs, "viewer_scope": scope}
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


@app.get("/api/approvals")
def approvals_list(user: str):
    try:
        return approvals.list_for(user)
    except Exception as e:
        _err(e)


@app.post("/api/approvals/{approval_id}/decide")
def approvals_decide(approval_id: int, body: Decide):
    try:
        return approvals.decide(approval_id, body.user, body.approve, body.note)
    except KeyError:
        raise HTTPException(404, "no such approval")
    except Exception as e:
        _err(e)


@app.get("/api/actions/{action_id}")
def action(action_id: int, user: str):
    try:
        if not (roles.can(user, "replay") or roles.can(user, "approve")):
            raise roles.Forbidden("only the manager or the vigilance officer can replay actions")
        a = browser_agent.get_action(action_id)
        if not a:
            raise HTTPException(404, "no such action")
        return a
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


@app.get("/api/screens/{action_id}/{name}")
def screen(action_id: int, name: str):
    path = (config.SCREENS / str(action_id) / name).resolve()
    if config.SCREENS.resolve() not in path.parents or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/api/audit")
def audit_log(user: str, case_id: int | None = None, limit: int = 200):
    try:
        u = roles.user(user)
        with db.state() as s:
            q, args = "SELECT * FROM audit", []
            if case_id:
                q, args = q + " WHERE case_id=?", [case_id]
            rows = [dict(r) for r in s.execute(q + " ORDER BY event_id DESC LIMIT ?", (*args, limit))]
        if u["role"] in ("auditor", "admin"):
            pass
        elif u["role"] == "manager":
            team = roles.team_of(user)
            rows = [r for r in rows if r["on_behalf_of"] in team]
        else:
            rows = [r for r in rows if r["on_behalf_of"] == user]
        for r in rows:
            r["detail"] = json.loads(r["detail"] or "{}")
        return rows
    except Exception as e:
        _err(e)


@app.get("/api/savings")
def savings():
    return budget.savings()


@app.get("/api/ledger")
def ledger(limit: int = 30):
    with db.state() as s:
        rows = [dict(r) for r in s.execute("SELECT * FROM ledger ORDER BY entry_id DESC LIMIT ?", (limit,))]
    for r in rows:
        r["user_name"] = roles.USERS.get(r["user_id"], {}).get("name")
    return rows


@app.get("/api/data/vendors")
def vendors(q: str = "", limit: int = 20):
    rows = data_agent.run(
        """SELECT v.vendor_id, v.name, v.cin, v.mca_status_label, v.registered_state, COUNT(a.award_id) AS awards
           FROM vendors v JOIN awards a ON a.vendor_id = v.vendor_id WHERE v.name LIKE ? GROUP BY v.vendor_id
           ORDER BY awards DESC LIMIT ?""", (f"%{q}%", min(limit, 100)), on_behalf_of="system", chain="UI lookup")
    return rows


@app.get("/api/gfr/{clause_id}")
def gfr(clause_id: str):
    c = document_agent.clause(clause_id)
    if not c:
        raise HTTPException(404)
    return c


@app.post("/api/upload")
async def upload(user: str = Form(...), case_id: int | None = Form(None), file: UploadFile = File(...)):
    try:
        roles.require(user, "upload")
        chain = audit.chain_for(roles.USERS[user]["name"], "Orchestrator", "Document Agent")
        data = await file.read()
        text, flags = document_agent.read_upload(file.filename, data, user, chain, case_id)
        with db.state() as s:
            cur = s.execute("INSERT INTO uploads(user_id, case_id, filename, text, flags, created_at) VALUES(?,?,?,?,?,?)",
                            (user, case_id, file.filename, text, json.dumps(flags), audit.now()))
        audit.log(user, chain, "Document Agent", "read_upload", {"file": file.filename, "instruction_like_lines": flags},
                  "FLAGGED – TREATED AS DATA, NOTHING EXECUTED" if flags else "OK", case_id)
        return {"upload_id": cur.lastrowid, "filename": file.filename, "text_preview": text[:1500], "flags": flags,
                "actions_taken": [],
                "note": ("Instruction-like text was found. It was stored as plain data and no action was taken."
                         if flags else "No instruction-like text found.")}
    except Exception as e:
        _err(e)


# ---- admin and demo ------------------------------------------------------------

def _admin(user):
    roles.require(user, "load_data")


@app.post("/api/admin/reset")
def admin_reset(user: str = "admin", start_rules: str = "after"):
    try:
        _admin(user)
        return demo.reset(start_rules)
    except Exception as e:
        _err(e)


@app.post("/api/admin/registry-update")
def admin_registry(user: str = "admin"):
    try:
        _admin(user)
        return demo.registry_update()
    except Exception as e:
        _err(e)


@app.post("/api/admin/gfr-amendment")
def admin_gfr(user: str = "admin"):
    try:
        _admin(user)
        return demo.gfr_amendment()
    except Exception as e:
        _err(e)


@app.get("/api/admin/change-log")
def admin_changes():
    return demo.change_log()


@app.get("/api/demo/sample-quotation.pdf")
def sample_pdf():
    return Response(demo.sample_quotation_pdf(), media_type="application/pdf",
                    headers={"Content-Disposition": "attachment; filename=quotation_sri_lakshmi_traders.pdf"})


@app.post("/api/demo/permission-test")
def permission_test():
    return demo.permission_test()


@app.post("/api/demo/expire-approvals")
def expire():
    return demo.expire_approvals()


@app.post("/api/demo/stuck-agent")
def stuck(user: str = "ravi"):
    return demo.stuck_agent(user)


FRONTEND = config.ROOT / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/", response_class=HTMLResponse)
def home():
    return FileResponse(FRONTEND / "index.html")
