# Auto GitHub Issue Solver Agent

An autonomous software-engineering agent capable of monitoring GitHub issues, searching the repository context via vector database & AST, developing a solution, testing in an isolated Docker sandbox, attempting repair on failures, and finally generating a Pull Request.

## Features
- GitHub Webhook integrations
- Code Retrieval with LangGraph & pgvector
- Safe code execution with Sandbox Containers
- Self-healing debug cyclic workflows
- Fully decoupled Event-Driven architecture

## GitHub App Setup Requirements
To interface securely with GitHub:
1. Create a GitHub App in your developer settings.
2. Generate and download a Private Key (`.pem` format).
3. Secure the App ID and Private Key, and set them in your `.env`:
   - `GITHUB_APP_ID=123456`
   - `GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----..."`
4. Set a strong, randomly generated webhook secret:
   - `GITHUB_WEBHOOK_SECRET=your_secret_here`
5. Map specific permissions: **Read & Write** to (Issues, Pull Requests, Repository Contents).
6. Enable Webhooks and configure your URL to point to `/webhooks/github` matching the `GITHUB_WEBHOOK_SECRET`.

## Setup

1. Copy `.env.example` config file:
   ```bash
   cp .env.example .env
   ```

2. Start the infrastructural containers (PostgreSQL+pgvector & Redis):
   ```bash
   docker-compose up -d
   ```

3. Setup Backend Environment:
   ```bash
   cd backend
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

## Running the Architecture
**1. API Server:**
```bash
cd backend
.\venv\Scripts\activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
**2. Background Job Worker:**
```bash
cd backend
.\venv\Scripts\activate
python -m app.workers.issue_worker
```

## Running the Test Suite
Tests execute mock interactions that decouple GitHub networking paths. 
```bash
cd backend
.\venv\Scripts\activate
pytest -v
```
