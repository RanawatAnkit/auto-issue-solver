import sys
import os
sys.path.append(os.getcwd())
from app.db.session import SessionLocal
from app.models.domain import AgentStateEnum
from sqlalchemy import text
db = SessionLocal()
for state in AgentStateEnum:
    try:
        db.execute(text(f"ALTER TYPE agentstateenum ADD VALUE '{state.value}';"))
        db.commit()
    except Exception as e:
        db.rollback()
print("Fixed ENUM")
