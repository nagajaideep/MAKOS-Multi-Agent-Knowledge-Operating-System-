"""Rule Agent: deterministic checks R1-R8 (no LLM).

Thresholds come from GFR 2017 and the DoE OM dated 10.07.2024. The rule version in force
("before" or "after" the 2024 amendment) is a setting, so a rule change can be loaded as a
demo event and only answers that used the rule check are refreshed.
"""
import json

from backend import config
from backend.database import db
from backend.governance import guards

AGENT = "Rule Agent"


def amendment():
    return json.loads(config.GFR_AMENDMENT.read_text())


def rules_version() -> str:
    return db.get_setting("gfr_version", "after")


def thresholds():
    return amendment()["thresholds"][rules_version()]


def rules_source_key() -> str:
    return "rules:gfr"


def _ok(code, msg, src):
    return {"code": code, "status": "ok", "message": msg, "sources": src}


def check_registry(v, on_behalf_of, chain, case_id=None):
    """R1: company must be Active in the MCA registry."""
    guards.use_tool(AGENT, "rules.evaluate", on_behalf_of, chain, case_id)
    src = [f"MCA:{v['cin']}"] if v.get("cin") else []
    if not v.get("cin"):
        return {"code": "R1", "status": "cannot_confirm",
                "message": "Not found in the MCA company registry (may be a proprietorship). Registry status cannot be confirmed.",
                "sources": src}
    if v["mca_status"] == "ACTV":
        return _ok("R1", f"Active in MCA registry (CIN {v['cin']}, registered {v['date_of_registration']}).", src)
    return {"code": "R1", "status": "fail", "exception": "VENDOR_NOT_ACTIVE",
            "message": f"MCA registry status is '{v['mca_status_label']}' (CIN {v['cin']}).", "sources": src}


def check_debarment(v, debarments, on_behalf_of, chain, case_id=None):
    """R2: not on a debarment list (GFR Rule 151). A name-only match is never treated as proof."""
    guards.use_tool(AGENT, "rules.evaluate", on_behalf_of, chain, case_id)
    if not debarments:
        return _ok("R2", "Not found on NSE/SEBI or World Bank debarment lists.", ["GFR-151"])
    confirmed = [d for d in debarments if d["pan"] and v.get("pan") and d["pan"] == v["pan"] and not v.get("pan_is_synthetic")]
    d = (confirmed or debarments)[0]
    src = ["GFR-151", f"LIST:{d['list_name']}"]
    if confirmed:
        return {"code": "R2", "status": "fail", "exception": "DEBARRED",
                "message": f"Debarred: {d['list_name']} (PAN {d['pan']}), from {d['start_date']}.", "sources": src, "pan": d["pan"]}
    return {"code": "R2", "status": "cannot_confirm", "exception": "POSSIBLE_DEBARMENT_MATCH",
            "message": (f"A firm with the same name is on the {d['list_name']} list"
                        f"{' (PAN ' + d['pan'] + ')' if d['pan'] else ''}, from {d['start_date']}. "
                        "No PAN or CIN links it to this vendor, so this cannot be confirmed. Needs human review."),
            "sources": src, "pan": d["pan"]}


def check_history(awards, on_behalf_of, chain, case_id=None):
    """R8: past awards; single-bid awards are a review note, not a violation."""
    guards.use_tool(AGENT, "rules.evaluate", on_behalf_of, chain, case_id)
    if not awards:
        return {"code": "R8", "status": "info", "message": "No past CPPP awards found for this vendor in the region since 2023.",
                "sources": [], "count": 0}
    single = [a for a in awards if a["bids_received"] == 1]
    total = sum(a["contract_value"] or 0 for a in awards)
    msg = f"{len(awards)} past award(s) on CPPP since 2023."
    if single:
        msg += f" {len(single)} received only one bid (review note, GFR Rule 162/166)."
    return {"code": "R8", "status": "note" if single else "ok", "message": msg, "count": len(awards),
            "single_bid": len(single), "total_value": total,
            "sources": [f"CPPP:{a['award_id']}" for a in awards[:3]] + (["GFR-162"] if single else []),
            "top_awards": [{"org": a["org_name"][:90], "title": a["title"][:110], "contract_value": a["contract_value"],
                            "bids": a["bids_received"], "date": a["contract_date"], "url": a["source_url"]} for a in awards[:3]]}


def purchase_method(amount: float, on_behalf_of, chain, case_id=None):
    """R3-R7: which GFR purchase method applies to an order of this value."""
    guards.use_tool(AGENT, "rules.evaluate", on_behalf_of, chain, case_id)
    t = thresholds()
    ver = "GFR 2017 as amended by OM dated 10.07.2024" if rules_version() == "after" else "GFR 2017 before the 10.07.2024 amendment"
    lakh = lambda x: f"₹{x / 1e5:g} lakh" if x >= 1e5 else f"₹{x:,.0f}"
    if amount <= t["without_quotation_upto"]:
        code, rule, method = "R4", "GFR-154", f"Purchase without quotation allowed (up to {lakh(t['without_quotation_upto'])}), with certificate."
    elif amount <= t["purchase_committee_upto"]:
        code, rule, method = "R5", "GFR-155", f"Purchase Committee of three members required (up to {lakh(t['purchase_committee_upto'])})."
    elif amount < t["advertised_tender_from"]:
        code, rule, method = "R6", "GFR-162", f"Limited Tender Enquiry (up to {lakh(t['limited_tender_upto'])}); invite more than three suppliers."
    else:
        code, rule, method = "R7", "GFR-161", f"Advertised (open) Tender Enquiry on GeM-CPPP required ({lakh(t['advertised_tender_from'])} and above)."
    gem = (f" If the item is on GeM, buy on GeM first (Rule 149: up to {lakh(t['gem_l1_seller_upto'])} from the lowest-price seller).")
    return {"code": code, "status": "ok", "message": method + gem, "amount": amount, "rules_version": ver,
            "sources": [rule, "GFR-149"], "method_rule": rule}


def decide(parts: dict) -> dict:
    """Overall decision. Any 'fail' blocks; any 'cannot_confirm' means a human must review."""
    checks = [p for p in parts.values() if p]
    if any(p["status"] == "fail" for p in checks):
        reasons = [p["exception"] for p in checks if p["status"] == "fail"]
        return {"decision": "DO_NOT_PLACE_ORDER", "label": "Do not place order", "reasons": reasons}
    if any(p["status"] == "cannot_confirm" for p in checks):
        return {"decision": "NEEDS_REVIEW", "label": "Cannot confirm – needs human review",
                "reasons": [p.get("exception", p["code"]) for p in checks if p["status"] == "cannot_confirm"]}
    return {"decision": "CAN_PROCEED", "label": "Can proceed", "reasons": []}
