import sys
import os
sys.path.append(os.getcwd())
import logging
logging.basicConfig(level=logging.INFO)
import requests
import jwt
import time
from unittest.mock import patch

os.environ["LLM_PROVIDER"] = "gemini"

from app.config import settings
from app.workers.issue_worker import process_agent_run
from app.db.session import SessionLocal
from app.models.domain import AgentRun, AgentStateEnum
from app.agents.schemas import IssueAnalysis, SolutionPlan, CodeChangePlan

db = SessionLocal()
# Target the REAL agent run directly isolated from Pytest limits!
run = db.query(AgentRun).filter(AgentRun.id == 35).first()

if run:
    run.status = AgentStateEnum.RECEIVED
    db.commit()
    
    # 1. Dynamically reconstruct Github App JWT natively 
    with open(settings.GITHUB_APP_PRIVATE_KEY, "rb") as key_file:
        private_key = key_file.read()
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (8 * 60),
        "iss": settings.GITHUB_APP_ID
    }
    encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")
    
    # 2. Extract Installation ID for the Target Repo properly
    repo = run.issue.repository
    owner, name = repo.full_name.split("/")
    
    headers = {
        "Authorization": f"Bearer {encoded_jwt}",
        "Accept": "application/vnd.github.v3+json"
    }
    resp = requests.get(f"https://api.github.com/repos/{repo.full_name}/installation", headers=headers)
    if resp.status_code == 200:
        installation_id = resp.json()["id"]
        print(f"Dynamically mapped installation_id {installation_id} for {repo.full_name}!")
        
        try:
            process_agent_run({"agent_run_id": run.id, "installation_id": installation_id})
        except Exception as e:
            print(f"PIPELINE CRASHED: {e}")
            raise e
    else:
        print(f"Failed to fetch installation ID. Are permissions correct? {resp.text}")
else:
    print("No AgentRun found. Pipeline skipped.")
