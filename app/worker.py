import asyncio
import logging
import shutil
import signal
from pathlib import Path

from bullmq import Job, Worker

from app import s3
from app.config import cfg
from app.hls import transcode
from app.probe import probe
from app.quality import vmaf, xpsnr

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("worker")


async def process(job: Job, token: str) -> dict:
    id, key, opts = str(job.id), job.data["key"], job.data.get("opts", {})
    work = cfg.tmp_dir / f"job_{id}"
    out = work / "hls"
    out.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()

    async def step(name: str, pct: float) -> None:
        await job.updateProgress({"step": name, "pct": round(pct, 1)})

    try:
        src = work / f"src{Path(key).suffix or '.mp4'}"
        await asyncio.to_thread(s3.get, cfg.raw_bucket, key, src)
        info = await asyncio.to_thread(probe, src)
        log.info(f"{id}: {info.width}x{info.height} {info.fps:.1f}fps {info.duration:.1f}s")
        await step("transcode", 10)

        def on_progress(p: float) -> None:
            asyncio.run_coroutine_threadsafe(step("transcode", 10 + p * 0.5), loop)

        rs, enc = await transcode(src, out, info, opts.get("segment", 4), opts.get("hw", False), on_progress)

        renditions = []
        for i, r in enumerate(rs):
            await step(f"quality {r.name}", 60 + 30 * i / len(rs))
            m3u8 = out / str(i) / "index.m3u8"
            q = {"name": r.name, "size": f"{r.width}x{r.height}", "kbps": r.kbps}
            if opts.get("vmaf", True):
                q["vmaf"] = await asyncio.to_thread(vmaf, m3u8, src)
            if opts.get("xpsnr", True):
                q["xpsnr"] = await asyncio.to_thread(xpsnr, m3u8, src, info)
            renditions.append(q)

        await step("upload", 90)
        await asyncio.to_thread(s3.put_dir, out, cfg.hls_bucket, id)
        await step("done", 100)
        playlist = s3.url(cfg.hls_bucket, f"{id}/master.m3u8")
        log.info(f"{id}: done, {enc}, {playlist}")
        return {"playlist": playlist, "encoder": enc, "renditions": renditions}
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def main() -> None:
    s3.ensure_buckets()
    worker = Worker(cfg.queue, process, {"connection": cfg.redis_url, "concurrency": 2})
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    log.info(f"listening on '{cfg.queue}'")
    await stop.wait()
    await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
