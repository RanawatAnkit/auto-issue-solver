from app.db.session import engine
from sqlalchemy import text

if __name__ == "__main__":
    with engine.connect() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector;'))
        conn.commit()
    print("Vector extension verified.")
