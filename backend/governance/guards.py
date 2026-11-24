"""Safety guards.

Feature 11 - fake-instruction blocker (documents are data, never commands)
Feature 12 - each agent gets only its own tools
SQL guard  - the data agent may run a single read-only SELECT over allow-listed tables only
"""
import re

import sqlglot
from sqlglot import exp

from backend.services import audit

# ---- feature 12: tool permissions per agent --------------------------------

AGENT_TOOLS = {
    "Orchestrator":   {"plan", "cache.read", "cache.write", "approval.request"},
    "Document Agent": {"docs.search", "docs.read_upload"},
    "Data Agent":     {"sql.read"},
    "Rule Agent":     {"rules.evaluate"},
    "Action Agent":   {"portal.browser"},
}


class ToolBlocked(Exception):
    pass


def use_tool(agent: str, tool: str, on_behalf_of: str, chain: str, case_id=None):
    """Raise ToolBlocked (and log it) unless `tool` is in the agent's allow-list."""
    if tool not in AGENT_TOOLS.get(agent, set()):
        audit.log(on_behalf_of, chain, agent, f"tool:{tool}", {"allowed": sorted(AGENT_TOOLS.get(agent, []))},
                  "BLOCKED", case_id)
        raise ToolBlocked(f"{agent} is not allowed to use {tool}")


# ---- SQL guard ----------------------------------------------------------------

ALLOWED_TABLES = {"vendors", "awards", "debarments"}
MAX_ROWS = 200


class UnsafeSQL(Exception):
    pass


def check_sql(sql: str) -> str:
    """Validate and return a safe version of the statement (adds a LIMIT)."""
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQL(f"could not parse SQL: {e}") from e
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise UnsafeSQL("exactly one statement is allowed")
    stmt = statements[0]
    if not isinstance(stmt, exp.Select):
        raise UnsafeSQL(f"only SELECT is allowed, got {stmt.key.upper()}")
    for node in stmt.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command)):
            raise UnsafeSQL("write or schema statements are not allowed")
    tables = {t.name.lower() for t in stmt.find_all(exp.Table)}
    bad = tables - ALLOWED_TABLES
    if bad:
        raise UnsafeSQL(f"table(s) not allowed: {', '.join(sorted(bad))}")
    if not stmt.args.get("limit"):
        stmt = stmt.limit(MAX_ROWS)
    return stmt.sql(dialect="sqlite")


# ---- feature 11: fake-instruction blocker --------------------------------------

INSTRUCTION_PATTERNS = [
    r"\bignore (all |any |the )?(previous |prior |above )?(rules|instructions|checks)\b",
    r"\b(system|assistant)\s*:",
    r"\bmark (this|the) (vendor|supplier|firm)\b.*\b(verified|approved|clear(ed)?)\b",
    r"\bskip (all |the )?(checks|verification|approval)\b",
    r"\bapprove (this|the) (vendor|order|payment)\b",
    r"\byou (are|must) now\b",
    r"\b(drop|delete|update)\s+table\b",
    r"\bdo not (tell|inform|show)\b",
]
_INSTR = [re.compile(p, re.I) for p in INSTRUCTION_PATTERNS]


def scan_for_instructions(text: str) -> list:
    """Return the lines that look like instructions to an AI system."""
    flags = []
    for i, line in enumerate(text.splitlines(), start=1):
        for pat in _INSTR:
            if pat.search(line):
                flags.append({"line": i, "text": line.strip()[:200], "pattern": pat.pattern})
                break
    return flags
