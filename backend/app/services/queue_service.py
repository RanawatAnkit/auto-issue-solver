import redis
import json
from app.config import settings

redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
QUEUE_NAME = "issue_jobs"

def enqueue_issue_job(payload: dict):
    val = json.dumps(payload)
    redis_client.lpush(QUEUE_NAME, val)

def dequeue_issue_job():
    result = redis_client.brpop(QUEUE_NAME, timeout=3)
    if result:
        return json.loads(result[1])
    return None