import asyncio
import logging
import shutil
import signal
import time
from pathlib import Path
from typing import Any, Dict

from bullmq import Job, Worker

from src.config import settings
from src.ffmpeg.hls import generate_adaptive_hls
from src.ffmpeg.metrics import evaluate_hls_stream_quality
from src.ffmpeg.probe import probe_video
from src.queue import is_job_cancelled
from src.s3 import s3_storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("worker")


async def process_video_job(job: Job, token: str) -> Dict[str, Any]:
    job_id = str(job.id)
    start_time = time.time()
    data = job.data or {}
    s3_bucket = data.get("s3_bucket", settings.S3_BUCKET_RAW)
    s3_key = data.get("s3_key")
    options = data.get("options", {})

    if not s3_key:
        raise ValueError(f"Job {job_id} missing s3_key")

    async def check_cancelled() -> None:
        if await is_job_cancelled(job_id):
            raise RuntimeError("Cancelled by user")

    await check_cancelled()
    logger.info(f"Job {job_id}: s3://{s3_bucket}/{s3_key}")
    work_dir = settings.temp_path / f"job_{job_id}"
    hls_dir = work_dir / "hls"
    work_dir.mkdir(parents=True, exist_ok=True)
    hls_dir.mkdir(parents=True, exist_ok=True)

    try:
        source_ext = Path(s3_key).suffix or ".mp4"
        local_source = work_dir / f"src{source_ext}"
        await asyncio.to_thread(s3_storage.download_file, s3_bucket, s3_key, local_source)
        await check_cancelled()
        await job.updateProgress({"step": "downloaded", "percent": 10})

        metadata = await asyncio.to_thread(probe_video, local_source)
        logger.info(f"[{job_id}] {metadata.width}x{metadata.height} {metadata.fps:.1f}fps {metadata.duration_seconds:.1f}s")
        await job.updateProgress({
            "step": "probed",
            "percent": 15,
            "metadata": {
                "width": metadata.width,
                "height": metadata.height,
                "duration": metadata.duration_seconds,
                "fps": metadata.fps,
                "codec": metadata.video_codec,
            },
        })

        seg_dur = options.get("segment_duration", settings.HLS_SEGMENT_TIME)
        hw_accel = bool(options.get("hw_accel", False))
        loop = asyncio.get_running_loop()

        def on_progress(pct: float) -> None:
            overall = 15.0 + (pct * 0.45)
            asyncio.run_coroutine_threadsafe(
                job.updateProgress({"step": "transcoding", "transcode_pct": round(pct, 1), "percent": round(overall, 1)}),
                loop,
            )

        profiles, encoder_used = await generate_adaptive_hls(
            local_source,
            hls_dir,
            segment_duration=seg_dur,
            progress_callback=on_progress,
            hw_accel=hw_accel,
            is_cancelled=lambda: is_job_cancelled(job_id),
        )
        await check_cancelled()
        await job.updateProgress({"step": "transcoded", "percent": 60})

        quality_report: Dict[str, Any] = {}
        if options.get("compute_quality_metrics", settings.COMPUTE_QUALITY_METRICS):
            await check_cancelled()
            await job.updateProgress({"step": "evaluating_quality", "percent": 65})
            quality_report = await evaluate_hls_stream_quality(
                reference_path=local_source,
                hls_dir=hls_dir,
                profiles=profiles,
                compute_vmaf_flag=options.get("compute_vmaf", settings.COMPUTE_VMAF),
                compute_xpsnr_flag=options.get("compute_xpsnr", settings.COMPUTE_XPSNR),
                vmaf_model=settings.VMAF_MODEL,
            )
            await check_cancelled()
            await job.updateProgress({"step": "quality_done", "percent": 85})

        s3_prefix = f"hls/{job_id}"
        await check_cancelled()
        await asyncio.to_thread(s3_storage.upload_directory, hls_dir, settings.S3_BUCKET_PROCESSED, s3_prefix)
        master_key = f"{s3_prefix}/master.m3u8"
        master_url = s3_storage.get_object_url(settings.S3_BUCKET_PROCESSED, master_key)
        await job.updateProgress({"step": "completed", "percent": 100})
        duration_sec = round(time.time() - start_time, 1)
        logger.info(f"[{job_id}] Done: {master_url} in {duration_sec}s (encoder: {encoder_used})")

        return {
            "job_id": job_id,
            "status": "success",
            "bucket": settings.S3_BUCKET_PROCESSED,
            "master_playlist_key": master_key,
            "master_playlist_url": master_url,
            "encoder": encoder_used,
            "hw_accel": hw_accel,
            "duration_seconds": duration_sec,
            "renditions": [{"name": p.name, "resolution": f"{p.width}x{p.height}", "bitrate_k": p.video_bitrate_k} for p in profiles],
            "quality_report": quality_report,
        }
            "quality_report": quality_report,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def run_worker() -> None:
    logger.info(f"Starting worker: queue={settings.QUEUE_NAME} redis={settings.REDIS_URL}")
    s3_storage.ensure_buckets()

    worker = Worker(settings.QUEUE_NAME, process_video_job, {"connection": settings.REDIS_URL, "concurrency": 2})
    stop = asyncio.Event()

    def on_stop():
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, on_stop)
        except NotImplementedError:
            pass

    logger.info("Worker ready")
    await stop.wait()
    await worker.close()
    logger.info("Worker stopped")


if __name__ == "__main__":
    asyncio.run(run_worker())
