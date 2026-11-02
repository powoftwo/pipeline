from typing import Any, Dict, List, Optional
import redis.asyncio as aioredis
from bullmq import Job, Queue

from src.config import settings

_queue: Optional[Queue] = None
_redis: Optional[aioredis.Redis] = None


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue(settings.QUEUE_NAME, {"connection": settings.REDIS_URL})
    return _queue


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


async def cancel_job(job_id: str) -> bool:
    r = get_redis()
    await r.set(f"job:cancel:{job_id}", "1", ex=300)
    job = await get_job_by_id(job_id)
    if job:
        try:
            state = await job.getState()
            if state in ("waiting", "delayed"):
                await job.remove()
        except Exception:
            pass
    return True


async def delete_job(job_id: str) -> bool:
    r = get_redis()
    await r.set(f"job:cancel:{job_id}", "1", ex=300)
    await r.delete(f"bull:{settings.QUEUE_NAME}:{job_id}:lock")
    job = await get_job_by_id(job_id)
    if job:
        try:
            await job.remove()
        except Exception:
            await r.delete(f"bull:{settings.QUEUE_NAME}:{job_id}:lock")
            try:
                await job.remove()
            except Exception:
                pass
    return True


async def is_job_cancelled(job_id: str) -> bool:
    try:
        return bool(await get_redis().exists(f"job:cancel:{job_id}"))
    except Exception:
        return False


def _extract_job_details(job: Job, state: str) -> Dict[str, Any]:
    ret = job.returnvalue or {}
    proc_on = getattr(job, "processedOn", None) or None
    fin_on = getattr(job, "finishedOn", None) or None
    dur = None
    if fin_on and proc_on and fin_on > proc_on:
        dur = round((fin_on - proc_on) / 1000.0, 1)
    elif isinstance(ret, dict) and ret.get("duration_seconds"):
        dur = ret.get("duration_seconds")

    return {
        "id": job.id,
        "name": job.name,
        "state": state,
        "progress": job.progress,
        "data": job.data,
        "return_value": ret,
        "failed_reason": job.failedReason,
        "timestamp": job.timestamp,
        "processed_on": proc_on,
        "finished_on": fin_on,
        "duration_seconds": dur,
        "attempts_made": job.attemptsMade,
    }


async def enqueue_video_job(
    s3_key: str,
    bucket: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
    job_id: Optional[str] = None,
) -> Job:
    queue = get_queue()
    data = {"s3_bucket": bucket or settings.S3_BUCKET_RAW, "s3_key": s3_key, "options": options or {}}
    opts: Dict[str, Any] = {
        "removeOnComplete": False,
        "removeOnFail": False,
        "attempts": 3,
        "backoff": {"type": "exponential", "delay": 2000},
    }
    if job_id:
        opts["jobId"] = job_id
    return await queue.add("process-video", data, opts)


async def get_job_by_id(job_id: str) -> Optional[Job]:
    return await get_queue().getJob(job_id)


async def get_job_status_details(job_id: str) -> Optional[Dict[str, Any]]:
    job = await get_job_by_id(job_id)
    if not job:
        return None
    state = await job.getState()
    return _extract_job_details(job, state)


async def list_jobs_details(limit: int = 20) -> List[Dict[str, Any]]:
    jobs = await get_queue().getJobs(["active", "waiting", "completed", "failed"], 0, limit - 1, False)
    res = []
    for job in jobs:
        try:
            state = await job.getState()
            res.append(_extract_job_details(job, state))
        except Exception:
            continue
    return res
