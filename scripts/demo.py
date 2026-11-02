"""Generate a test clip, upload it (multipart or --tus), print the result."""
import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

API = "http://localhost:8000"


def make_clip(path: Path) -> None:
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=duration=60:size=1280x720:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=60",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ], check=True)


def upload_multipart(path: Path, hw: bool = False) -> str:
    with path.open("rb") as f:
        r = httpx.post(f"{API}/jobs", files={"file": (path.name, f, "video/mp4")}, data={"segment": 2, "hw": str(hw).lower()}, timeout=60)
    r.raise_for_status()
    return r.json()["id"]


def upload_tus(path: Path, hw: bool = False) -> str:
    from tusclient import client
    up = client.TusClient(f"{API}/tus").uploader(
        str(path),
        chunk_size=256 * 1024,
        metadata={"filename": path.name, "segment": "2", "hw": str(hw).lower()},
    )
    up.upload()
    return up.url.rstrip("/").split("/")[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tus", action="store_true", help="use resumable upload")
    parser.add_argument("--hw", "--hw-accel", "--hwaccel", dest="hw", action="store_true", help="hardware acceleration")
    parser.add_argument("--timestamps", "--ts", dest="timestamps", action="store_true", default=True, help="timestamps (on)")
    parser.add_argument("--no-timestamps", dest="timestamps", action="store_false", help="no timestamps")
    args = parser.parse_args()

    def ts() -> str:
        return f"[{time.strftime('%H:%M:%S')}] " if args.timestamps else ""

    with tempfile.TemporaryDirectory() as tmp:
        clip = Path(tmp) / "demo.mp4"
        make_clip(clip)
        upload = upload_tus if args.tus else upload_multipart
        id = upload(clip, hw=args.hw)
    print(f"{ts()}job {id}")

    last = None
    while True:
        job = httpx.get(f"{API}/jobs/{id}").json()
        line = f"{job['state']} {(job.get('progress') or {}).get('step', '')}"
        if line != last:
            print(f"{ts()}  {line}")
            last = line
        if job["state"] == "failed":
            sys.exit(job["error"])
        if job["state"] == "completed":
            break
        time.sleep(1)

    r = job["result"]
    print(f"{ts()}{r['playlist']} ({r['encoder']})")
    for q in r["renditions"]:
        print(f"  {q['name']:>6} {q['size']:>9} {q['kbps']}k  vmaf={q.get('vmaf', {}).get('mean')}  xpsnr_y={q.get('xpsnr', {}).get('y')}")


if __name__ == "__main__":
    main()
