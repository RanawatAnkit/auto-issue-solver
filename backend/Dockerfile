FROM python:3.11-slim

WORKDIR /app

# git is used by the worker (clone/commit/push); gcc and the Postgres headers build wheels that lack binaries
RUN apt-get update && apt-get install -y \
    git \
    gcc \
    postgresql-server-dev-all \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose FastAPI default port
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
