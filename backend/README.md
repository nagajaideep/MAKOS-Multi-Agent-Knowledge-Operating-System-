# MAKOS backend

## Run

```bash
pip install -r requirements.txt
uvicorn backend.main:app --port 8000
```

- API docs: http://127.0.0.1:8000/docs
- Demo vendor portal (target of the browser agent): http://127.0.0.1:8000/portal
- Without Anthropic credentials MAKOS runs in **offline mode**: answers are written from a template and tokens are estimated. With `ANTHROPIC_API_KEY` set, answers are written by Claude and real token usage is recorded. Set `MAKOS_OFFLINE=1` to force offline mode.
- The browser agent uses the pre-installed Chromium (`MAKOS_CHROMIUM` to change the path) and calls the server at `MAKOS_BASE_URL` (default `http://127.0.0.1:8000`).

## Tests

```bash
python3 -m pytest -q tests/
```

## Map of features to code

| # | Feature | Code |
|---|---|---|
| 1–4 | Shared answers, refresh only when asked, part-by-part refresh, what changed | `memory/answer_layer.py`, `agents/orchestrator.py` |
| 5 | Duplicate work detection | `agents/orchestrator.py` (`open_case`) |
| 6–8 | Role-based access, role-aware answers, masking | `governance/roles.py` |
| 9 | On-behalf-of tracking | `services/audit.py` |
| 10 | No evidence, no answer | `agents/rule_agent.py` (`cannot_confirm`), `orchestrator.py` |
| 11 | Fake-instruction blocker | `governance/guards.py` (`scan_for_instructions`), `agents/document_agent.py` |
| 12 | Each agent gets only its own tools; read-only SQL guard | `governance/guards.py` |
| 13 | Approval only for risky actions, escalation | `workflows/approvals.py` |
| 14–15 | Browser agent with verification and screenshot replay | `workflows/browser_agent.py`, `workflows/portal.py` |
| 16–17 | Token budget, auto-stop, savings meter | `services/budget.py`, `services/demo.py` (`stuck_agent`) |

## Demo API sequence

| Scene | Call |
|---|---|
| Reset | `POST /api/admin/reset?user=admin` (add `&start_rules=before` to replay the 2024 GFR amendment) |
| 1–2, 4–5 | `POST /api/ask {"user": "ravi", "question": "Can I place a Rs 18 lakh order with Teleconcepts (India) Pvt Ltd?"}` |
| 3 | `POST /api/cases/start {"user": "sneha", "vendor": "Teleconcepts (India) Pvt Ltd"}` |
| 5 | `POST /api/admin/registry-update?user=admin`, then ask again |
| 6 | `GET /api/approvals?user=rao`, `POST /api/approvals/{id}/decide {"user": "rao", "approve": true}`, `GET /api/actions/{id}?user=priya` |
| 7 | `GET /api/demo/sample-quotation.pdf`, `POST /api/upload` (form: user, file), `POST /api/demo/permission-test` |
| 8 | `POST /api/demo/stuck-agent?user=ravi`, `GET /api/savings` |
