"""Central settings for the MAKOS demonstrator."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_env_file(path: Path) -> None:
    """Read KEY=VALUE lines from a local .env file (git-ignored) without overriding real env vars."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(ROOT / ".env")
DATA_DB = ROOT / "data/processed/makos.db"          # real data (vendors, awards, debarments)
GFR_CLAUSES = ROOT / "data/processed/gfr_clauses.json"
GFR_AMENDMENT = ROOT / "data/processed/gfr_amendment_2024.json"
RUNTIME = Path(os.getenv("MAKOS_RUNTIME", ROOT / "data/runtime"))  # created at start, not committed
STATE_DB = RUNTIME / "state.db"                      # cache, cases, chats, audit, approvals, ledger
WORK_DB = RUNTIME / "work.db"                        # working copy of DATA_DB that demo events change
SCREENS = RUNTIME / "screens"

# LLM (optional): writes the short answer from evidence that was already checked.
# MAKOS_LLM_PROVIDER = gemini | claude | offline. Without a key MAKOS runs offline and estimates tokens.
LLM_PROVIDER = os.getenv("MAKOS_LLM_PROVIDER", "gemini" if os.getenv("GEMINI_API_KEY") else "claude").lower()
LLM_MODEL = os.getenv("MAKOS_MODEL", "claude-opus-5-5")          # used when provider = claude
LLM_EFFORT = os.getenv("MAKOS_EFFORT", "low")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")    # used when provider = gemini
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-flash-lite-latest")  # used when the first is busy

# Cost display (USD per million tokens). Defaults: Claude Opus 5.5 list price, or an assumed
# Gemini Flash price - set MAKOS_PRICE_IN_PER_M_USD / MAKOS_PRICE_OUT_PER_M_USD to your price list.
_DEFAULT_PRICE = {"claude": (4.00, 20.00), "gemini": (0.30, 2.50)}.get(LLM_PROVIDER, (4.00, 20.00))
PRICE_IN_PER_M_USD = float(os.getenv("MAKOS_PRICE_IN_PER_M_USD", _DEFAULT_PRICE[0]))
PRICE_OUT_PER_M_USD = float(os.getenv("MAKOS_PRICE_OUT_PER_M_USD", _DEFAULT_PRICE[1]))
USD_TO_INR = float(os.getenv("MAKOS_USD_INR", "85"))

# Approvals not decided within this many minutes escalate to the vigilance officer.
APPROVAL_TIMEOUT_MIN = float(os.getenv("MAKOS_APPROVAL_TIMEOUT_MIN", "10"))

# Token budget per department (tokens per demo session).
DEPT_BUDGETS = {"Purchase": 60000, "Vigilance": 20000}

CHROMIUM = os.getenv("MAKOS_CHROMIUM", "/opt/pw-browsers/chromium-1243/chrome-linux64/chrome")
