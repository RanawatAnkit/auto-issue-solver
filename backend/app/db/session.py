from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.config import settings

_connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    # The FastAPI test client and worker threads share connections.
    _connect_args = {"check_same_thread": False}

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
