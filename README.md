# Auto GitHub Issue Solver Agent

An agent that takes a GitHub issue, analyzes it, retrieves relevant code from the repository,
plans a change, applies validated edits in an isolated clone, runs the repository's tests in a
Docker sandbox, repairs failures within a bounded budget, and opens a reviewable pull request.

## Pipeline

```
webhook / dashboard "Solve"
  → Redis queue → worker
  → clone repo, list tracked files, index (pgvector, best effort)
  → issue analysis (LLM) → context retrieval (named files + semantic + lexical)
  → plan (LLM, every path validated against git-tracked files)        ── stops here if ENABLE_CODE_MUTATION=false (PLAN_READY)
  → baseline tests on the untouched checkout (Docker, no network)       ── stops if the sandbox cannot run the tests
  → code generation as search/replace edits, dry-run validated, retried with feedback
  → tests → bounded repair loop (failure analysis → repair plan → edits → tests)
  → validate the real git diff (scope, secrets)
  → branch, commit, push, PR                                             ── skipped if ENABLE_PR_CREATION=false (COMPLETED)
```

A run only reaches `REPAIR_SUCCESS` / `PR_CREATED` when the sandbox tests actually pass.
Every step is recorded in the run's step log (`GET /agent-runs/{id}/actions`).

## GitHub App Setup Requirements
1. Create a GitHub App in your developer settings.
2. Generate and download a Private Key (`.pem`).
3. Set in `.env`:
   - `GITHUB_APP_ID=123456`
   - `GITHUB_APP_PRIVATE_KEY=` path to the `.pem` file, or the PEM text itself
4. Set a strong webhook secret: `GITHUB_WEBHOOK_SECRET=...` (webhooks are rejected without it).
5. Permissions: **Read & Write** for Issues, Pull Requests and Contents; Read for Metadata.
6. Subscribe to **Issues** events and point the webhook URL at `/webhooks/github`.
7. For dashboard login, create a GitHub OAuth App and set `GITHUB_OAUTH_CLIENT_ID` / `GITHUB_OAUTH_CLIENT_SECRET`.

## Setup

1. Configuration: `cp .env.example .env` and fill it in (see comments in the file).
2. Infrastructure (PostgreSQL+pgvector and Redis):
   ```bash
   docker compose up -d
   ```
3. Sandbox image (used to run repository tests):
   ```bash
   docker build -t test-sandbox:latest sandbox
   ```
4. Backend:
   ```bash
   cd backend
   python -m venv venv
   venv\Scripts\activate            # Linux/macOS: source venv/bin/activate
   pip install -r requirements.txt
   alembic upgrade head             # creates/updates the schema (safe to re-run)
   ```
5. Local LLM (default provider):
   ```bash
   ollama pull qwen2.5-coder:7b
   ollama serve                     # if it is not already running
   ```
6. Frontend:
   ```bash
   cd frontend
   npm install
   ```

## Running

| Component | Command (from the repo root) |
|---|---|
| PostgreSQL + Redis | `docker compose up -d` |
| API | `cd backend && venv\Scripts\activate && uvicorn app.main:app --reload --port 8000` |
| Worker (one instance) | `cd backend && venv\Scripts\activate && python -m app.workers.issue_worker` |
| Dashboard | `cd frontend && npm run dev` → http://localhost:5173 |

Check that everything is reachable: `GET http://localhost:8000/health/dependencies`.

Run exactly one worker: at startup it marks runs interrupted by a previous worker crash as
`FAILED` (they are not retried automatically; use **Re-run** in the dashboard).

## Safety switches

- `ENABLE_CODE_MUTATION=false` (default): analysis and planning only; nothing is modified.
- `ENABLE_PR_CREATION=false`: verify changes locally but never push or open a PR.
- Fallback to a paid LLM provider only happens with `LLM_ALLOW_PAID_FALLBACK=true`.

## Running the Test Suite

```bash
cd backend
venv\Scripts\activate
pytest -q                                   # isolated SQLite DB, never the dev database
```

Optional integration runs:

```bash
# PostgreSQL + pgvector (needs a dedicated database whose name contains "test")
set TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/auto_issue_solver_test
pytest -q
# Live call to the configured local Ollama model
set RUN_OLLAMA_TESTS=1
pytest -q -k live
```

Tests marked `docker` run only when a Docker engine is reachable; skipped tests always print why.

Frontend checks: `cd frontend && npm run lint && npm run build`.
