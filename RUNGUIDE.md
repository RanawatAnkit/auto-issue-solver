# Run guide

Practical steps to set up, run, test, evaluate and debug Auto-Solver. Commands are for Windows
(PowerShell or cmd) from the repository root unless a step says otherwise; on Linux/macOS use
`source venv/bin/activate` and forward slashes.

## 1. Prerequisites

| Tool | Version used during development | Needed for |
|---|---|---|
| Python | 3.13 (3.11+ works) | backend |
| Node.js / npm | 22 / 10 | dashboard |
| Docker Desktop | 29 (Compose v2) | Postgres, Redis, test sandboxes, SWE-bench |
| Git | any recent | cloning repos (worker and harness) |
| Ollama | optional | local LLM (`qwen2.5-coder:7b`, ~5 GB) |

Disk: the SWE-bench cache and images take a few GB (four repository clones, one image per
repo/version).

## 2. Configuration

```
copy .env.example .env
```

Fill in `.env` (it is git-ignored). Minimum for each use:

- **Tests only:** nothing.
- **SWE-bench harness:** an LLM (`LLM_PROVIDER` plus a key, or Ollama) and Docker.
- **Full app:** additionally the database settings, a GitHub App (`GITHUB_APP_ID`,
  `GITHUB_APP_PRIVATE_KEY` = path to the `.pem`), `GITHUB_WEBHOOK_SECRET`, and a GitHub OAuth app
  for dashboard login (`GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`).

Ollama settings:

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder:7b
```

Do **not** add `/v1` to `OLLAMA_BASE_URL`; the client builds its own paths and a `/v1` suffix
turns every request into a 404 that looks like "model not found".

Environment variables override `.env`, which is handy for one-off runs:
`set LLM_PROVIDER=ollama` (cmd), `$env:LLM_PROVIDER="ollama"` (PowerShell),
`LLM_PROVIDER=ollama python ...` (bash).

Restart the API and the worker after changing `.env`.

GitHub App permissions: Issues, Pull requests and Contents read & write; Metadata read.
Subscribe it to **Issues** events and point its webhook at `https://<host>/webhooks/github`.

## 3. Install

```
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cd ..\frontend
npm install
```

## 4. Infrastructure

```
docker compose up -d                          # PostgreSQL (pgvector) on 5432, Redis on 6379
docker build -t test-sandbox:latest sandbox   # base image for the worker's test sandbox
cd backend
venv\Scripts\activate
alembic upgrade head                          # safe to re-run
alembic current                               # should print the same id as `alembic heads`
```

## 5. Run the application

Three terminals, each with the venv activated in `backend\`:

```
uvicorn app.main:app --reload --port 8000     # API, docs at http://localhost:8000/docs
python -m app.workers.issue_worker            # worker: run exactly one
cd ..\frontend && npm run dev                 # dashboard at http://localhost:5173
```

If you use Ollama: `ollama serve` (or start the Ollama app) and `ollama pull qwen2.5-coder:7b` once.

Check everything is reachable:

```
curl http://localhost:8000/health/dependencies
```

All checks should be `"ok": true`. Note that the LLM check for Gemini/OpenAI only confirms a key
is set; it cannot see an exhausted quota.

For live GitHub webhooks on a laptop, expose port 8000 with a tunnel (e.g. ngrok) and set the
App's webhook URL to the tunnel address. The tunnel binary is not part of the repository.

Flow: log in → **Repositories** → connect `owner/name` (the App must be installed there) → open
an issue → **Solve**. Keep `ENABLE_CODE_MUTATION=false` until you have watched a run reach
`PLAN_READY`; set `ENABLE_PR_CREATION=false` to stop at `COMPLETED` without pushing.

## 6. Tests and checks

```
cd backend
venv\Scripts\activate
pytest -q                          # ~1 minute; isolated SQLite DB, no services required
pytest -q tests\test_eval_harness.py tests\test_eval_api.py   # harness only
```

Optional integration tests:

```
set TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/auto_issue_solver_test
pytest -q                          # pgvector tests (the database name must contain "test")
set RUN_OLLAMA_TESTS=1
pytest -q -k live                  # one real call to the configured Ollama model
```

Tests marked `docker` run when Docker is reachable. Every skip prints its reason (`pytest -rs`).

Frontend:

```
cd frontend
npm run lint                       # zero warnings allowed
npx tsc -b                         # type check
npm run build
```

There is no Python linter, formatter or type checker configured in this repository.

## 7. SWE-bench

All commands from `backend\` with the venv active. Docker must be running. The first run of a
repository clones it into `backend\.eval_cache\` and builds its images; later runs reuse them.

### 7.1 Validate the environments first

Status at the time of writing: 31 of 32 tasks validate (all Flask, pytest and requests tasks,
5 of 6 pylint tasks).

```
python -m app.eval validate --repo pallets/flask
python -m app.eval validate --allow-network        # all tasks; psf/requests needs network
```

This grades the maintainers' fixes. A task whose reference fix is not `RESOLVED` cannot judge
the agent; the dashboard marks it **INVALID** and leaves it out of the attempted-rate.

### 7.2 Run the agent

```
python -m app.eval run pallets__flask-4045         # one task
python -m app.eval run --repo pytest-dev/pytest    # one repository
python -m app.eval run --allow-network             # the whole benchmark
python -m app.eval run pallets__flask-4045 --timeout 600
```

Each line printed is one result: instance, outcome, seconds, detail. A batch stops early (exit
code 1) on an LLM quota or authentication error.

Timing: grading takes 15–250 s per task; the agent stage depends on the model (a 7B model on a
6 GB laptop GPU needs several minutes per task).

### 7.3 Results

```
python -m app.eval report          # headline numbers
python -m app.eval list            # every task: environment check and latest agent outcome
```

The dashboard's **Evaluation** page shows the same data and can start runs (one at a time, in the
API process; poll-based progress, "Stop after current task").

Outcomes: `RESOLVED`, `UNRESOLVED`, `NO_PATCH`, `PATCH_APPLY_FAILED` are agent results;
`LLM_ERROR`, `ENV_ERROR`, `HARNESS_ERROR` are infrastructure failures and do not count against
the agent. See the table in [README.md](README.md#swe-bench-evaluation).

### 7.4 Inspect a failed evaluation

Files under `backend\eval_results\`:

```
history.jsonl                                   one JSON result per line, newest last
runs\<run_id>\<instance_id>.agent.patch         the agent's diff
runs\<run_id>\<instance_id>.agent.log           full pytest output
runs\<run_id>\<instance_id>.gold.patch / .log   same for validation runs
```

Each history record has `outcome`, `detail`, `tests.f2p_failed` / `tests.p2p_failed`, the
agent's `analysis` and `plan`, `stage_seconds` and `artifacts` (the path prefix above).

Reading a result:

- `NO_PATCH` — read `detail`: plan validation errors, edits rejected by the patcher (with the
  reason for each attempt), or the model never produced valid JSON.
- `UNRESOLVED` — look at `tests.f2p_failed`, then the `.log` for the traceback, then the
  `.patch` to see what the agent changed. Compare with the reference fix (`patch` in
  `app\eval\benchmark_tasks.json`, or the dashboard's **Reference fix** tab).
- `ENV_ERROR` — Docker not running, a pip install failure (the build log is in `detail`), or a
  network task run without `--allow-network`.
- `LLM_ERROR` — quota, key or model name. Fix the provider, then rerun the tasks.

### 7.5 Reproduce a failed task by hand

```
python -m app.eval validate psf__requests-2148 --allow-network   # is the environment sound?
python -m app.eval run psf__requests-2148 --allow-network
```

To apply a recorded patch yourself:

```
git clone .eval_cache\repos\psf__requests %TEMP%\req
cd %TEMP%\req
git checkout <base_commit from benchmark_tasks.json>
git apply <path>\eval_results\runs\<run_id>\psf__requests-2148.agent.patch
```

### 7.6 Regenerate the dataset

```
python scripts\build_swebench_subset.py
```

Downloads SWE-bench Lite from Hugging Face and rewrites `app\eval\benchmark_tasks.json` with the
instance IDs listed in the script. Add environments for new repositories in
`app\eval\environments.py` and validate them before adding their tasks.

## 8. Logs and debugging agent runs

- API and worker log to their terminals. Each LLM call logs provider, model, schema, attempt
  and duration; failures log the (secret-scrubbed) error.
- Each worker run's steps are in the database: dashboard **Agent Runs** → a run, or
  `GET /agent-runs/{id}/actions`. Inputs and outputs of every step (analysis, plan, patch
  attempts, test results, repair attempts) are recorded there.
- `error_message` and `termination_reason` on the run say why it stopped (`NO_VALID_PATCH`,
  `ENVIRONMENT_FAILURE`, `REPEATED_FAILURE`, `MAX_ATTEMPTS`, `TIME_BUDGET`, `LLM_ERROR`).
- Harness runs log to the terminal; results and artifacts are described in 7.4.

## 9. Reset generated state

```
rmdir /s /q backend\eval_results        # evaluation history (no undo)
rmdir /s /q backend\.eval_cache         # cloned benchmark repositories
docker image prune -a --filter label=auto-issue-solver=sandbox   # sandbox and SWE-bench images
docker compose down -v                  # deletes the Postgres and Redis volumes (no undo)
```

## 10. Common problems

| Symptom | Cause / fix |
|---|---|
| Every eval task is `LLM_ERROR` "quota" | Provider quota is spent. The batch stops after the first one. Switch provider (e.g. `LLM_PROVIDER=ollama`) or wait. |
| "Ollama model ... not found" although `ollama list` shows it | `OLLAMA_BASE_URL` ends in `/v1`; remove it. |
| `Prompt too large for Ollama context window` | Raise `OLLAMA_NUM_CTX` (and make sure the model supports it). |
| Ollama times out | 7B models on small GPUs are slow; raise `OLLAMA_TIMEOUT`. |
| `ENV_ERROR: Docker engine is not reachable` | Start Docker Desktop. |
| `ENV_ERROR` mentioning `--allow-network` | The task's tests call external services; rerun with `--allow-network`. |
| Gold validation `UNRESOLVED` | Either the environment is wrong (fix `environments.py`, usually a missing or too-new package) or an external service misbehaved: httpbin.org occasionally breaks single `psf/requests` tests, so validate again before concluding. `pylint-dev__pylint-7080` is permanently invalid here: one expected test ID contains an absolute path from the original environment. |
| `NO_PATCH` "cut off at OLLAMA_NUM_PREDICT" | The model kept generating until the output limit. Common with 7B models on large edits; raise `OLLAMA_NUM_PREDICT` (and `OLLAMA_NUM_CTX` with it) or use a stronger model. |
| Worker run stuck in `QUEUED` | The worker is not running, or Redis is unreachable (`/health/dependencies`). |
| Run `FAILED` with "Sandbox cannot run this repository's tests" | The worker sandbox could not install or start the repo's tests. Check the run's `baseline_tests` step. |
| `[WinError 10013]` from uvicorn | Port 8000 is taken: `Get-NetTCPConnection -LocalPort 8000` and stop that process, or use `--port`. |
| Login loops back to the login page | OAuth app callback must be `GITHUB_OAUTH_REDIRECT_URI`, and `FRONTEND_URL` must match the dashboard URL. |
| `409` connecting a repository | Another user already connected it. |

## 11. Development workflow

1. Branch from `main`.
2. Make a small change with a test next to it (`backend\tests\`).
3. `pytest -q`, and for frontend changes `npm run lint` and `npm run build`.
4. For agent, prompt or patcher changes: run a few harness tasks before and after
   (`python -m app.eval run --repo pallets/flask`) and compare outcomes, not just unit tests.
5. Check `git status` before committing: no `.env`, `.pem`, `eval_results\` or cache files.

## 12. Troubleshooting checklist

1. `docker ps` shows Postgres and Redis; Docker Desktop is running.
2. `curl http://localhost:8000/health/dependencies` is all `ok`.
3. `alembic current` equals `alembic heads`.
4. LLM: `ollama list` shows the model, or the cloud key has quota left.
5. Exactly one worker is running.
6. `.env` changes were followed by restarting the API and worker.
7. For evaluation: `python -m app.eval validate <id>` passes before you look at agent results.
