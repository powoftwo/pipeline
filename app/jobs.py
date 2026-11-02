from bullmq import Queue

from app.config import cfg

_queue: Queue | None = None


def queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue(cfg.queue, {"connection": cfg.redis_url})
    return _queue


async def enqueue(id: str, key: str, opts: dict) -> None:
    await queue().add(
        "hls",
        {"key": key, "opts": opts},
        {"jobId": id, "attempts": 3, "backoff": {"type": "exponential", "delay": 2000}},
    )


async def status(id: str) -> dict | None:
    job = await queue().getJob(id)
    if not job:
        return None
    return {
        "id": job.id,
        "state": await job.getState(),
        "progress": job.progress,
        "result": job.returnvalue,
        "error": job.failedReason,
    }
