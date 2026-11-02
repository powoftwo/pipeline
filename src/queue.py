from typing import Any, Dict, Optional
from bullmq import Job, Queue

from src.config import settings

_queue: Optional[Queue] = None


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue(settings.QUEUE_NAME, {"connection": settings.REDIS_URL})
    return _queue


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
    return {
        "id": job.id,
        "name": job.name,
        "state": state,
        "progress": job.progress,
        "data": job.data,
        "return_value": job.returnvalue,
        "failed_reason": job.failedReason,
        "timestamp": job.timestamp,
        "attempts_made": job.attemptsMade,
    }
