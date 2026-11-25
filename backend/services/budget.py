"""Token budget, auto-stop (feature 16) and the savings meter (feature 17)."""
from backend import config
from backend.database import db
from backend.services import audit


class BudgetExceeded(Exception):
    pass


def check(dept: str) -> None:
    with db.state() as s:
        b = s.execute("SELECT * FROM budgets WHERE dept=?", (dept,)).fetchone()
    if b and (b["stopped"] or b["used_tokens"] >= b["limit_tokens"]):
        raise BudgetExceeded(f"{dept} department token budget used up ({b['used_tokens']}/{b['limit_tokens']})")


def charge(dept: str, tokens: int) -> None:
    with db.state() as s:
        s.execute("UPDATE budgets SET used_tokens = used_tokens + ? WHERE dept=?", (tokens, dept))


def record(user_id, dept, kind, question, tokens_in, tokens_out, baseline, parts_total, parts_refreshed, ms, llm_mode):
    with db.state() as s:
        s.execute(
            """INSERT INTO ledger(ts, user_id, dept, kind, question, tokens_in, tokens_out, baseline_tokens,
               parts_total, parts_refreshed, ms, llm_mode) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (audit.now(), user_id, dept, kind, question, tokens_in, tokens_out, baseline, parts_total,
             parts_refreshed, ms, llm_mode),
        )


def cost_inr(tokens_in: int, tokens_out: int) -> float:
    usd = tokens_in / 1e6 * config.PRICE_IN_PER_M_USD + tokens_out / 1e6 * config.PRICE_OUT_PER_M_USD
    return round(usd * config.USD_TO_INR, 2)


def savings() -> dict:
    with db.state() as s:
        rows = [dict(r) for r in s.execute("SELECT * FROM ledger ORDER BY entry_id")]
        budgets = [dict(r) for r in s.execute("SELECT * FROM budgets")]
    asked = [r for r in rows if r["kind"] in ("full_run", "reuse", "refresh", "new_scope")]
    used_in = sum(r["tokens_in"] for r in asked)
    used_out = sum(r["tokens_out"] for r in asked)
    used = used_in + used_out
    # Baseline: a system without the shared answer layer does a full run for every question.
    baseline = sum(r["baseline_tokens"] for r in asked)
    share_in = used_in / used if used else 0.8
    base_in, base_out = int(baseline * share_in), int(baseline * (1 - share_in))
    ms_reuse = [r["ms"] for r in asked if r["kind"] == "reuse"]
    ms_full = [r["ms"] for r in asked if r["kind"] == "full_run"]
    return {
        "questions": len(asked),
        "full_runs": sum(r["kind"] == "full_run" for r in asked),
        "reused": sum(r["kind"] == "reuse" for r in asked),
        "refreshed": sum(r["kind"] == "refresh" for r in asked),
        "new_scope": sum(r["kind"] == "new_scope" for r in asked),
        "parts_refreshed": sum(r["parts_refreshed"] or 0 for r in asked),
        "tokens_used": used,
        "tokens_without_makos": baseline,
        "tokens_saved": max(0, baseline - used),
        "percent_saved": round(100 * (baseline - used) / baseline, 1) if baseline else 0.0,
        "cost_inr": cost_inr(used_in, used_out),
        "cost_without_makos_inr": cost_inr(base_in, base_out),
        "avg_ms_reused": round(sum(ms_reuse) / len(ms_reuse), 1) if ms_reuse else None,
        "avg_ms_full_run": round(sum(ms_full) / len(ms_full), 1) if ms_full else None,
        "llm_modes": sorted({r["llm_mode"] for r in asked if r["llm_mode"]}),
        "budgets": budgets,
        "blocked_or_stopped": sum(r["kind"] in ("stopped", "blocked") for r in rows),
        "price_note": f"Priced at ${config.PRICE_IN_PER_M_USD}/M input and ${config.PRICE_OUT_PER_M_USD}/M output tokens "
                      f"({config.LLM_PROVIDER}; set MAKOS_PRICE_* in .env to your price list), ₹{config.USD_TO_INR}/$",
    }
