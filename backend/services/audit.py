"""Audit log with on-behalf-of tracking (feature 9).

Every agent step records the human who started it and the chain of agents in between,
e.g. "Ravi → Orchestrator → Data Agent".
"""
import json
from datetime import datetime

from backend.database import db


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def log(on_behalf_of, chain, actor, action, detail=None, outcome="OK", case_id=None):
    with db.state() as s:
        s.execute(
            "INSERT INTO audit(ts, on_behalf_of, chain, actor, action, detail, outcome, case_id) VALUES(?,?,?,?,?,?,?,?)",
            (now(), on_behalf_of, chain, actor, action, json.dumps(detail or {}, default=str), outcome, case_id),
        )


def chain_for(user_name: str, *agents: str) -> str:
    return " → ".join([user_name, *agents])
