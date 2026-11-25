"""End-to-end test of the 8 demo scenes and the PS1 test cases, on the real data.

Runs a real server (the browser agent needs one) in offline LLM mode with a temporary
runtime folder, so it never touches data/runtime.
"""
import os
import socket
import tempfile
import threading
import time

import pytest

PORT = None


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def api():
    global PORT
    PORT = _free_port()
    os.environ["MAKOS_RUNTIME"] = tempfile.mkdtemp(prefix="makos-test-")
    os.environ["MAKOS_OFFLINE"] = "1"
    os.environ["MAKOS_BASE_URL"] = f"http://127.0.0.1:{PORT}"
    import httpx
    import uvicorn

    from backend.main import app

    server = uvicorn.Server(uvicorn.Config(app, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    client = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=60)
    client.post("/api/admin/reset", params={"user": "admin"})
    yield client
    server.should_exit = True


def ask(api, user, q):
    r = api.post("/api/ask", json={"user": user, "question": q})
    assert r.status_code == 200, r.text
    return r.json()


VENDOR_A = "Teleconcepts (India) Pvt Ltd"


def test_scene1_first_question_full_run_with_evidence(api):
    d = ask(api, "ravi", f"Can I place a Rs 18 lakh order for transformer oil with {VENDOR_A}?")
    assert d["kind"] == "full_run" and d["decision"]["decision"] == "CAN_PROCEED"
    assert [p["type"] for p in d["parts"]] == ["registry", "debarment", "history", "rule"]
    assert all(p["sources"] for p in d["parts"])                       # no evidence, no answer
    assert d["chain"].startswith("Ravi → Orchestrator")                   # on-behalf-of
    assert d["parts"][3]["details"]["method_rule"] == "GFR-162"           # ₹18 lakh -> limited tender


def test_scene2_same_question_other_words_is_reused_for_free(api):
    d = ask(api, "sneha", f"Is {VENDOR_A} okay to buy from for 18 lakh?")
    assert d["kind"] == "reuse" and d["tokens"]["total"] == 0
    assert d["joined"] and "already has an open case" in d["joined"]["message"]


def test_scene3_duplicate_case_is_joined(api):
    r = api.post("/api/cases/start", json={"user": "sneha", "vendor": VENDOR_A}).json()
    assert r["case_id"] == 1 and r["joined"]["already_member"]


def test_scene4_role_aware_answer_and_masking(api):
    officer = ask(api, "ravi", f"Can I place a Rs 18 lakh order with {VENDOR_A}?")
    manager = ask(api, "rao", f"Can we place an 18 lakh order with {VENDOR_A}?")
    assert officer["scope"] == "masked" and manager["scope"] == "full"
    assert manager["kind"] == "new_scope" and all(p["state"] == "reused" for p in manager["parts"])
    reg_o = officer["parts"][0]["details"]["bank_account"]
    reg_m = manager["parts"][0]["details"]["bank_account"]
    assert reg_o.startswith("XXXXXX") and reg_m.startswith("DEMO")
    # an officer cannot open a case he is not part of; the manager can see the team's chats
    case_b = ask(api, "sneha", "Can we give a 3 lakh furniture repair order to Global Enterprises?")["case_id"]
    assert api.get(f"/api/cases/{case_b}", params={"user": "ravi"}).status_code == 403
    assert api.get(f"/api/cases/{case_b}", params={"user": "rao"}).status_code == 200


def test_name_only_debarment_match_is_never_called_debarred(api):
    d = ask(api, "sneha", "Can we give a 3 lakh furniture repair order to Global Enterprises?")
    deb = next(p for p in d["parts"] if p["type"] == "debarment")
    assert deb["status"] == "cannot_confirm" and "cannot be confirmed" in deb["message"]
    assert "XXXXX" in deb["message"]                                     # PAN masked for an officer
    assert d["decision"]["decision"] == "NEEDS_REVIEW"
    assert d["proposed_action"]["risk"] == "low"                          # review note, no hold


def test_scene5_refresh_only_when_asked_and_only_changed_part(api):
    upd = api.post("/api/admin/registry-update", params={"user": "admin"}).json()
    assert [c["name"] for c in upd["changed_rows"]] == [VENDOR_A]
    d = ask(api, "ravi", f"Can I place a Rs 18 lakh order with {VENDOR_A}?")
    assert d["kind"] == "refresh" and d["parts_refreshed"] == 1
    assert {p["type"]: p["state"] for p in d["parts"]} == {"registry": "refreshed", "debarment": "reused",
                                                            "history": "reused", "rule": "reused"}
    assert d["changes_since_last_time"][0] == {**d["changes_since_last_time"][0], "before": "Active", "after": "Strike Off"}
    assert d["decision"]["decision"] == "DO_NOT_PLACE_ORDER"
    assert d["proposed_action"]["action"] == "hold_vendor" and d["proposed_action"]["status"] == "pending"


def test_scene6_approval_gate_and_verified_browser_action(api):
    aid = next(a for a in api.get("/api/approvals", params={"user": "rao"}).json() if a["action"] == "hold_vendor")["approval_id"]
    assert api.post(f"/api/approvals/{aid}/decide", json={"user": "ravi", "approve": True}).status_code == 403
    r = api.post(f"/api/approvals/{aid}/decide", json={"user": "rao", "approve": True, "note": "struck off"}).json()
    assert r["action"]["verified"] is True and len(r["action"]["screenshots"]) == 6
    replay = api.get(f"/api/actions/{r['action']['action_id']}", params={"user": "priya"}).json()
    assert replay["status"] == "verified"
    assert api.get(replay["screenshots"][-1]["file"]).status_code == 200


def test_scene7_fake_instruction_and_tool_permissions(api):
    pdf = api.get("/api/demo/sample-quotation.pdf").content
    r = api.post("/api/upload", data={"user": "ravi"}, files={"file": ("q.pdf", pdf, "application/pdf")}).json()
    assert r["flags"] and r["actions_taken"] == []
    results = api.post("/api/demo/permission-test").json()
    assert all(x["result"] == "BLOCKED" for x in results)


def test_scene8_auto_stop_and_savings(api):
    s = api.post("/api/demo/stuck-agent", params={"user": "ravi"}).json()
    assert s["stopped"] and "loop" in s["reason"]
    sv = api.get("/api/savings").json()
    assert sv["reused"] >= 1 and sv["tokens_saved"] > 0 and sv["parts_refreshed"] >= 1


def test_gfr_amendment_refreshes_only_the_rule_part(api):
    api.post("/api/admin/reset", params={"user": "admin", "start_rules": "before"})
    q = "Order worth 30 lakh with Verder Scientific Private Limited?"
    before = ask(api, "ravi", q)
    assert before["parts"][3]["details"]["method_rule"] == "GFR-161"      # ≥ ₹25 lakh: advertised tender (old)
    api.post("/api/admin/gfr-amendment", params={"user": "admin"})
    after = ask(api, "ravi", q)
    assert after["parts"][3]["state"] == "refreshed" and after["parts_refreshed"] == 1
    assert after["parts"][3]["details"]["method_rule"] == "GFR-162"       # ≤ ₹50 lakh: limited tender (new)


def test_unsafe_sql_rejected():
    from backend.governance.guards import UnsafeSQL, check_sql
    assert "LIMIT" in check_sql("SELECT name FROM vendors")
    for bad in ["UPDATE vendors SET name='x'", "SELECT * FROM vendors; DELETE FROM awards", "SELECT * FROM state"]:
        with pytest.raises(UnsafeSQL):
            check_sql(bad)
