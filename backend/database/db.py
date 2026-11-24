"""Database connections.

work.db  - a working copy of the real data. Demo events (a registry update) change rows
           here and bump `row_version`, so the shared answer layer can see what changed.
state.db - everything MAKOS itself keeps: stored answers and their parts, cases, chats,
           approvals, browser actions, audit log, token ledger, budgets, rule version.
"""
import shutil
import sqlite3
from contextlib import contextmanager

from backend import config

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS answer_parts (
  part_key TEXT PRIMARY KEY, part_type TEXT, vendor_id INTEGER, params TEXT,
  result TEXT, sources TEXT, created_at TEXT, refreshed_at TEXT, computed_for TEXT, refresh_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS answers (
  answer_key TEXT, scope TEXT, intent TEXT, vendor_id INTEGER, amount REAL,
  part_keys TEXT, part_versions TEXT, text TEXT, decision TEXT, created_by TEXT, created_at TEXT,
  updated_at TEXT, hits INTEGER DEFAULT 0, PRIMARY KEY(answer_key, scope)
);
CREATE TABLE IF NOT EXISTS cases (
  case_id INTEGER PRIMARY KEY, vendor_id INTEGER, vendor_name TEXT, purpose TEXT, status TEXT,
  created_by TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS case_members (case_id INTEGER, user_id TEXT, joined_at TEXT, how TEXT, PRIMARY KEY(case_id, user_id));
CREATE TABLE IF NOT EXISTS messages (
  msg_id INTEGER PRIMARY KEY, case_id INTEGER, user_id TEXT, sender TEXT, text TEXT, meta TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  approval_id INTEGER PRIMARY KEY, case_id INTEGER, action TEXT, params TEXT, risk TEXT, reason TEXT,
  requested_for TEXT, status TEXT, assigned_to TEXT, decided_by TEXT, decided_at TEXT, decision_note TEXT,
  created_at TEXT, due_at TEXT
);
CREATE TABLE IF NOT EXISTS actions (
  action_id INTEGER PRIMARY KEY, approval_id INTEGER, kind TEXT, status TEXT, verified INTEGER,
  screenshots TEXT, log TEXT, started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS audit (
  event_id INTEGER PRIMARY KEY, ts TEXT, on_behalf_of TEXT, chain TEXT, actor TEXT, action TEXT,
  detail TEXT, outcome TEXT, case_id INTEGER
);
CREATE TABLE IF NOT EXISTS ledger (
  entry_id INTEGER PRIMARY KEY, ts TEXT, user_id TEXT, dept TEXT, kind TEXT, question TEXT,
  tokens_in INTEGER, tokens_out INTEGER, baseline_tokens INTEGER, parts_total INTEGER,
  parts_refreshed INTEGER, ms REAL, llm_mode TEXT
);
CREATE TABLE IF NOT EXISTS budgets (dept TEXT PRIMARY KEY, limit_tokens INTEGER, used_tokens INTEGER DEFAULT 0, stopped INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS uploads (
  upload_id INTEGER PRIMARY KEY, user_id TEXT, case_id INTEGER, filename TEXT, text TEXT, flags TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS portal_vendors (vendor_id INTEGER PRIMARY KEY, name TEXT, status TEXT, hold_reason TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS change_log (
  change_id INTEGER PRIMARY KEY, ts TEXT, source_key TEXT, field TEXT, old_value TEXT, new_value TEXT, note TEXT
);
"""


def _connect(path, readonly=False):
    uri = f"file:{path}?mode=ro" if readonly else f"file:{path}"
    con = sqlite3.connect(uri, uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


@contextmanager
def state():
    con = _connect(config.STATE_DB)
    try:
        yield con
        con.commit()
    finally:
        con.close()


@contextmanager
def work(readonly=True):
    """Business data. The data agent only ever gets a read-only connection."""
    con = _connect(config.WORK_DB, readonly=readonly)
    try:
        yield con
        if not readonly:
            con.commit()
    finally:
        con.close()


def init(reset: bool = False) -> None:
    config.RUNTIME.mkdir(parents=True, exist_ok=True)
    config.SCREENS.mkdir(parents=True, exist_ok=True)
    if reset:
        for p in (config.STATE_DB, config.WORK_DB):
            if p.exists():
                p.unlink()
        for d in config.SCREENS.glob("*"):
            shutil.rmtree(d, ignore_errors=True)
    if not config.WORK_DB.exists():
        shutil.copy(config.DATA_DB, config.WORK_DB)
    with state() as s:
        s.executescript(STATE_SCHEMA)
        for dept, limit in config.DEPT_BUDGETS.items():
            s.execute("INSERT OR IGNORE INTO budgets(dept, limit_tokens) VALUES(?,?)", (dept, limit))


def get_setting(key, default=None):
    with state() as s:
        r = s.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default


def set_setting(key, value):
    with state() as s:
        s.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (key, str(value)))
