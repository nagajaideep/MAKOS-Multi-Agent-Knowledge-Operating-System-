"""Users, roles, what each role may see, and masking.

Feature 6  - role-based access
Feature 7  - role-aware shared answers (the scope is part of the answer key)
Feature 8  - masking by role
"""
import re

USERS = {
    "ravi":  {"name": "Ravi",    "title": "Purchase Officer, Ramagundam", "role": "officer",  "dept": "Purchase",  "manager": "rao"},
    "sneha": {"name": "Sneha",   "title": "Purchase Officer, Vizag",      "role": "officer",  "dept": "Purchase",  "manager": "rao"},
    "rao":   {"name": "Mr. Rao", "title": "Deputy GM Purchase",           "role": "manager",  "dept": "Purchase",  "manager": None},
    "priya": {"name": "Priya",   "title": "Vigilance Officer",            "role": "auditor",  "dept": "Vigilance", "manager": None},
    "admin": {"name": "Admin",   "title": "System Administrator",         "role": "admin",    "dept": "IT",        "manager": None},
}

PERMISSIONS = {
    "officer": {"ask", "start_case", "view_own_cases", "upload"},
    "manager": {"ask", "start_case", "view_team_cases", "approve", "view_full_values", "upload"},
    "auditor": {"ask", "view_all_cases", "view_audit", "view_full_values", "replay", "decide_escalated"},
    "admin":   {"load_data", "manage_budgets", "view_audit"},
}


class Forbidden(Exception):
    pass


def user(user_id: str) -> dict:
    if user_id not in USERS:
        raise Forbidden(f"unknown user {user_id!r}")
    return {"id": user_id, **USERS[user_id]}


def can(user_id: str, perm: str) -> bool:
    return perm in PERMISSIONS[user(user_id)["role"]]


def require(user_id: str, perm: str) -> None:
    if not can(user_id, perm):
        raise Forbidden(f"{USERS[user_id]['name']} ({USERS[user_id]['role']}) may not {perm.replace('_', ' ')}")


def scope(user_id: str) -> str:
    """Answer scope. Answers are only ever shared between people with the same scope."""
    return "full" if can(user_id, "view_full_values") else "masked"


def team_of(manager_id: str) -> set:
    return {uid for uid, u in USERS.items() if u["manager"] == manager_id} | {manager_id}


def can_view_case(user_id: str, members: set) -> bool:
    u = user(user_id)
    if u["role"] == "auditor":
        return True
    if u["role"] == "manager":
        return bool(members & team_of(user_id))
    return user_id in members


# ---- masking -------------------------------------------------------------

def mask_pan(pan):
    return f"XXXXX{pan[5:]}" if pan and len(pan) == 10 else pan


def mask_account(acc):
    return f"XXXXXX{acc[-4:]}" if acc else acc


def mask_amount(v):
    if v is None:
        return None
    lakh = v / 1e5
    if lakh < 1:
        return "below ₹1 lakh"
    if lakh < 10:
        return "₹1–10 lakh"
    if lakh < 50:
        return "₹10–50 lakh"
    if lakh < 100:
        return "₹50 lakh–1 crore"
    return "above ₹1 crore"


SENSITIVE = {"pan": mask_pan, "bank_account": mask_account, "total_value": mask_amount, "contract_value": mask_amount,
             "amount": mask_amount, "paidup_capital": mask_amount, "authorized_capital": mask_amount}


def mask_dict(d, scope_name: str):
    """Return a copy of d with sensitive fields masked for the 'masked' scope (recursively)."""
    if scope_name == "full":
        return d
    if isinstance(d, list):
        return [mask_dict(x, scope_name) for x in d]
    if not isinstance(d, dict):
        return d
    out = {}
    for k, v in d.items():
        if k in SENSITIVE and not isinstance(v, (dict, list)):
            out[k] = SENSITIVE[k](v)
            out.setdefault("_masked", []).append(k)
        else:
            out[k] = mask_dict(v, scope_name)
    return out


PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")


def mask_text(text: str, scope_name: str) -> str:
    if scope_name == "full" or not text:
        return text
    return PAN_RE.sub(lambda m: mask_pan(m.group(0)), text)
