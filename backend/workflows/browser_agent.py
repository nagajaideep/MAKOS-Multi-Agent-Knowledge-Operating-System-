"""Action Agent: a browser agent for portals with no API (features 14 and 15).

It operates the vendor portal page like a person would (open, search, open the vendor,
type the reason, click Hold), saves a screenshot after every step, and then RE-OPENS the
page to verify that the status really says "On Hold" instead of trusting the click.

Only reachable through an approved request (see approvals.decide). Tool allowed: portal.browser.
"""
import json
import os

from backend import config
from backend.database import db
from backend.governance import guards, roles
from backend.services import audit

AGENT = "Action Agent"
BASE_URL = os.getenv("MAKOS_BASE_URL", "http://127.0.0.1:8000")


def hold_vendor(approval_id, vendor_id, vendor_name, reason, on_behalf_of, approved_by, case_id):
    chain = audit.chain_for(roles.USERS[on_behalf_of]["name"], "Orchestrator", f"Approval Gate ({roles.USERS[approved_by]['name']})", AGENT)
    guards.use_tool(AGENT, "portal.browser", on_behalf_of, chain, case_id)
    with db.state() as s:
        cur = s.execute("INSERT INTO actions(approval_id, kind, status, verified, screenshots, log, started_at) VALUES(?,?,?,?,?,?,?)",
                        (approval_id, "hold_vendor", "running", 0, "[]", "[]", audit.now()))
        action_id = cur.lastrowid
    folder = config.SCREENS / str(action_id)
    folder.mkdir(parents=True, exist_ok=True)
    shots, log = [], []

    def snap(page, n, label):
        path = folder / f"{n:02d}.png"
        page.screenshot(path=str(path))
        shots.append({"step": n, "label": label, "file": f"/api/screens/{action_id}/{n:02d}.png"})
        log.append(f"{n}. {label}")

    verified, error = False, None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=config.CHROMIUM if os.path.exists(config.CHROMIUM) else None)
            page = browser.new_page(viewport={"width": 1100, "height": 720})
            page.goto(f"{BASE_URL}/portal")
            snap(page, 1, "Opened the vendor portal")
            page.fill("#q", vendor_name[:30])
            page.click("#search")
            page.wait_for_load_state()
            snap(page, 2, f"Searched for '{vendor_name[:30]}'")
            page.click(f"a[data-vendor='{vendor_id}']")
            page.wait_for_load_state()
            snap(page, 3, "Opened the vendor record")
            page.fill("#reason", f"MAKOS case #{case_id}, approval #{approval_id}: {reason}"[:400])
            snap(page, 4, "Entered the reason")
            page.click("#hold")
            page.wait_for_load_state()
            snap(page, 5, "Clicked 'Put on hold'")
            page.goto(f"{BASE_URL}/portal/vendor/{vendor_id}")      # verify by reading the page again
            status = page.inner_text("#status").strip()
            verified = status.lower() == "on hold"
            snap(page, 6, f"Re-opened the record to verify: status shows '{status}'")
            browser.close()
    except Exception as e:  # keep the evidence of what happened
        error = str(e)[:300]
        log.append(f"error: {error}")

    status = "verified" if verified else "unverified"
    with db.state() as s:
        s.execute("UPDATE actions SET status=?, verified=?, screenshots=?, log=?, finished_at=? WHERE action_id=?",
                  (status, int(verified), json.dumps(shots), json.dumps(log), audit.now(), action_id))
        if case_id:
            s.execute("INSERT INTO messages(case_id, user_id, sender, text, meta, created_at) VALUES(?,?,?,?,?,?)",
                      (case_id, on_behalf_of, "system",
                       f"Action Agent put {vendor_name} on hold in the vendor portal and "
                       f"{'verified the status by re-opening the record' if verified else 'could NOT verify it - escalated'}.",
                       json.dumps({"action_id": action_id}), audit.now()))
    audit.log(on_behalf_of, chain, AGENT, "hold_vendor", {"action_id": action_id, "steps": log, "error": error},
              "VERIFIED" if verified else "NOT VERIFIED – ESCALATED", case_id)
    return {"action_id": action_id, "verified": verified, "screenshots": shots, "log": log, "error": error}


def get_action(action_id):
    with db.state() as s:
        r = s.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["screenshots"] = json.loads(d["screenshots"])
    d["log"] = json.loads(d["log"])
    return d
