# Auto Issue Solver: Complete Project Running Guide

This guide explains how to start and operate the **Auto Issue Solver** system using either:
1. **Local LLM Mode** (via **Ollama** running `qwen2.5-coder:7b` on your machine — 100% offline, free, and private).
2. **Cloud API Mode** (via **Google Gemini** or **OpenAI**).

Both modes share the same core backend, frontend dashboard, and Docker sandbox architecture.

---

## Table of Contents
1. [Local LLM vs. Cloud API: Comparison](#1-local-llm-vs-cloud-api-comparison)
2. [Switching Providers in `.env`](#2-switching-providers-in-env)
3. [Guide A: Running with Local LLM (Ollama)](#3-guide-a-running-with-local-llm-ollama)
4. [Guide B: Running with Cloud API (Gemini / OpenAI)](#4-guide-b-running-with-cloud-api-gemini--openai)
5. [How to Trigger Issue Solving & Testing](#5-how-to-trigger-issue-solving--testing)
6. [Troubleshooting & Common Questions](#6-troubleshooting--common-questions)

---

## 1. Local LLM vs. Cloud API: Comparison

| Feature | Local LLM (Ollama `qwen2.5-coder:7b`) | Cloud API (Gemini 3.6 Flash / OpenAI) |
| :--- | :--- | :--- |
| **Cost** | 100% Free | Pay-per-token or API free tier limits |
| **Privacy & Security** | Code never leaves your computer | Code snippets sent to cloud provider |
| **Internet Dependency** | Works offline (no external API calls) | Requires active internet & API keys |
| **Hardware Required** | Needs ~6-8 GB available RAM / VRAM | Runs on any machine (inference is remote) |
| **Code Quality** | Highly competent for standard tasks | Top-tier reasoning on complex edge cases |

---

## 2. Switching Providers in `.env`

The root [`.env`](file:///c:/sem7/proj/auto-issue-solver/.env) file controls which LLM powers the agent. Toggle between them simply by commenting or uncommenting the lines:

### To Use Local LLM (Ollama):
```env
# --- LLM Provider Selection ---
# [ACTIVE] Option 1: Local LLM (Ollama)
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5-coder:7b
OLLAMA_BASE_URL=http://localhost:11434/v1

# [INACTIVE] Option 2: Cloud API (Gemini / OpenAI)
# LLM_PROVIDER=gemini
# GEMINI_MODEL=gemini-3.6-flash
# GEMINI_API_KEY=your_gemini_api_key_here
# OPENAI_API_KEY=
```

### To Use Cloud API (Gemini):
```env
# --- LLM Provider Selection ---
# [INACTIVE] Option 1: Local LLM (Ollama)
# LLM_PROVIDER=ollama
# OLLAMA_MODEL=qwen2.5-coder:7b
# OLLAMA_BASE_URL=http://localhost:11434/v1

# [ACTIVE] Option 2: Cloud API (Gemini / OpenAI)
LLM_PROVIDER=gemini
GEMINI_MODEL=gemini-3.6-flash
GEMINI_API_KEY=your_gemini_api_key_here
# OPENAI_API_KEY=
```

### Pipeline Execution Flags (Required for End-to-End Solving):
Make sure both flags are set to `true` in your `.env` so the agent actively writes code and opens PRs:
```env
ENABLE_CODE_MUTATION=true   # If false, agent stops after planning (PLAN_READY)
ENABLE_PR_CREATION=true     # Opens a GitHub Pull Request after tests pass
```

> **Note:** Whenever you change `.env`, always restart your **Backend API** and **Issue Worker** terminals so the new variables take effect.

---

## 3. Guide A: Running with Local LLM (Ollama)

### Step 1: Ensure Ollama is Running with Your Model
Make sure the Ollama application is active in your system tray, or start it in PowerShell:
```powershell
ollama run qwen2.5-coder:7b
```
*(If it gives you a chat prompt, you can press `Ctrl+D` or `/bye` to exit the chat; the Ollama server remains active in the background at `http://localhost:11434`).*

To verify Ollama is serving requests:
```powershell
ollama list
```

---

### Step 2: Start Infrastructure (PostgreSQL, Redis & Sandbox)
Make sure **Docker Desktop** is running, then open Terminal 1 in the project root:
```powershell
cd c:\sem7\proj\auto-issue-solver
docker-compose up -d
```
Verify the containers are healthy:
```powershell
docker ps
```
*(You should see `auto-issue-solver-postgres-1` and `auto-issue-solver-redis-1`).*

**Run database migrations to ensure the latest schema:**
```powershell
cd c:\sem7\proj\auto-issue-solver\backend
.\venv\Scripts\activate
alembic upgrade head
```

**Build the sandbox test runner image (one-time setup):**
```powershell
cd c:\sem7\proj\auto-issue-solver
docker build -t test-sandbox:latest sandbox/
```

---

### Step 3: Start the Backend API Server
Open Terminal 2:
```powershell
cd c:\sem7\proj\auto-issue-solver\backend
.\venv\Scripts\activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
- API Docs: **[http://localhost:8000/docs](http://localhost:8000/docs)**
- Health Check: **[http://localhost:8000/health](http://localhost:8000/health)**

---

### Step 4: Start the Background Issue Worker
Open Terminal 3:
```powershell
cd c:\sem7\proj\auto-issue-solver\backend
.\venv\Scripts\activate
python -m app.workers.issue_worker
```
*(You will see: `INFO:__main__:Initializing Autoscale Worker Processor...`). This worker connects to Ollama directly at `http://localhost:11434/v1` to plan, code, and repair issues.*

---

### Step 5: Start the Frontend Dashboard
Open Terminal 4:
```powershell
cd c:\sem7\proj\auto-issue-solver\frontend
npm run dev
```
- Open your browser to: **[http://localhost:5173](http://localhost:5173)**

---

### Step 6: (Optional) Public Webhook Tunnel for Live GitHub Events
If you want live issues created on GitHub to automatically reach your laptop:
```powershell
cd c:\sem7\proj\auto-issue-solver
.\ngrok.exe http 8000
```
*(Copy the generated `https://...ngrok-free.dev/webhooks/github` URL and update the Webhook URL in your GitHub App settings).*

---

## 4. Guide B: Running with Cloud API (Gemini / OpenAI)

### Step 1: Update `.env` to Cloud API Mode
Open [`.env`](file:///c:/sem7/proj/auto-issue-solver/.env), comment out Ollama, and uncomment Gemini:
```env
# LLM_PROVIDER=ollama
# OLLAMA_MODEL=qwen2.5-coder:7b
# OLLAMA_BASE_URL=http://localhost:11434/v1

LLM_PROVIDER=gemini
GEMINI_MODEL=gemini-3.6-flash
GEMINI_API_KEY=your_gemini_api_key_here
```

---

### Step 2: Start Infrastructure (Docker)
In Terminal 1:
```powershell
cd c:\sem7\proj\auto-issue-solver
docker-compose up -d
```

---

### Step 3: Start Backend API Server
In Terminal 2:
```powershell
cd c:\sem7\proj\auto-issue-solver\backend
.\venv\Scripts\activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

### Step 4: Start the Issue Worker
In Terminal 3:
```powershell
cd c:\sem7\proj\auto-issue-solver\backend
.\venv\Scripts\activate
python -m app.workers.issue_worker
```

---

### Step 5: Start Frontend Dashboard
In Terminal 4:
```powershell
cd c:\sem7\proj\auto-issue-solver\frontend
npm run dev
```
- Open: **[http://localhost:5173](http://localhost:5173)**

---

## 5. How to Trigger Issue Solving & Testing

1. Open **[http://localhost:5173](http://localhost:5173)**.
2. Click **"Continue with GitHub"** to authenticate via your team's GitHub App.
3. Click on **Repositories** in the sidebar.
4. Select your connected repository (e.g. `Ankit7474738/taskflow-api`).
5. Choose an open issue (e.g. Issue #1) and click **"Solve Issue"**.
6. The system will dispatch the job:
   - Clones the target repository branch.
   - Builds context and feeds it to the LLM (Ollama or Gemini).
   - Generates code patches.
   - Spins up a safe Docker container sandbox (`python:3.11-slim`) to run tests.
   - Automatically repairs code if tests fail.
   - Commits changes and opens a GitHub Pull Request!

---

## 6. Troubleshooting & Common Questions

### Q: Why did uvicorn give `[WinError 10013]`?
**A:** Port 8000 was already bound by another running process or background service. Run this in PowerShell to find and kill what's using port 8000:
```powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object LocalPort, OwningProcess
Stop-Process -Id <OwningProcessId> -Force
```

### Q: Can I use other models in Ollama?
**A:** Yes! Any model that supports structured outputs / JSON mode (like `deepseek-coder:6.7b`, `llama3:8b`, `codellama`) can be used. Simply pull the model:
```powershell
ollama pull deepseek-coder:6.7b
```
And update `OLLAMA_MODEL=deepseek-coder:6.7b` in your `.env`.

### Q: Is Ollama fast enough for local development?
**A:** Yes. `qwen2.5-coder:7b` is optimized for programming logic, syntax, and instruction following, and runs fast on modern GPUs or CPU with 8+ GB RAM.
