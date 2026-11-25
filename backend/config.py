"""Central settings for the MAKOS demonstrator."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DB = ROOT / "data/processed/makos.db"          # real data (vendors, awards, debarments)
GFR_CLAUSES = ROOT / "data/processed/gfr_clauses.json"
GFR_AMENDMENT = ROOT / "data/processed/gfr_amendment_2024.json"
RUNTIME = Path(os.getenv("MAKOS_RUNTIME", ROOT / "data/runtime"))  # created at start, not committed
STATE_DB = RUNTIME / "state.db"                      # cache, cases, chats, audit, approvals, ledger
WORK_DB = RUNTIME / "work.db"                        # working copy of DATA_DB that demo events change
SCREENS = RUNTIME / "screens"

# LLM (optional). Without credentials MAKOS runs in offline mode and estimates tokens.
LLM_MODEL = os.getenv("MAKOS_MODEL", "claude-opus-5-5")
LLM_EFFORT = os.getenv("MAKOS_EFFORT", "low")

# Cost display: Claude Opus 5.5 list price per million tokens, converted at an assumed rate.
PRICE_IN_PER_M_USD = 4.00
PRICE_OUT_PER_M_USD = 20.00
USD_TO_INR = float(os.getenv("MAKOS_USD_INR", "85"))

# Approvals not decided within this many minutes escalate to the vigilance officer.
APPROVAL_TIMEOUT_MIN = float(os.getenv("MAKOS_APPROVAL_TIMEOUT_MIN", "10"))

# Token budget per department (tokens per demo session).
DEPT_BUDGETS = {"Purchase": 60000, "Vigilance": 20000}

CHROMIUM = os.getenv("MAKOS_CHROMIUM", "/opt/pw-browsers/chromium-1243/chrome-linux64/chrome")
