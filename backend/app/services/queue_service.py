import redis
import json
import logging
from app.config import settings

logger = logging.getLogger(__name__)

redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
QUEUE_NAME = "issue_jobs"
# Jobs are moved atomically into this list while a worker handles them, so a
# worker crash leaves them recoverable instead of silently dropping them.
PROCESSING_QUEUE_NAME = "issue_jobs:processing"

def enqueue_issue_job(payload: dict):
    val = json.dumps(payload)
    redis_client.lpush(QUEUE_NAME, val)

def dequeue_issue_job():
    """Return (payload, raw) for the next job, or None. Call ack_issue_job(raw) when done."""
    raw = redis_client.brpoplpush(QUEUE_NAME, PROCESSING_QUEUE_NAME, timeout=3)
    if not raw:
        return None
    try:
        return json.loads(raw), raw
    except json.JSONDecodeError:
        logger.error("Discarding malformed job payload: %r", raw[:200])
        ack_issue_job(raw)
        return None

def ack_issue_job(raw: str):
    redis_client.lrem(PROCESSING_QUEUE_NAME, 1, raw)

def drain_interrupted_jobs() -> list[dict]:
    """Remove and return jobs left in the processing list by a worker that died mid-run."""
    interrupted = []
    while True:
        raw = redis_client.rpop(PROCESSING_QUEUE_NAME)
        if raw is None:
            break
        try:
            interrupted.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.error("Discarding malformed interrupted job payload: %r", raw[:200])
    return interrupted
