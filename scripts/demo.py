"""Generate a test clip, upload it (multipart or --tus), print the result."""
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
        "-f", "lavfi", "-i", "testsrc=duration=4:size=1280x720:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ], check=True)


def upload_multipart(path: Path) -> str:
    with path.open("rb") as f:
        r = httpx.post(f"{API}/jobs", files={"file": (path.name, f, "video/mp4")}, data={"segment": 2}, timeout=60)
    r.raise_for_status()
    return r.json()["id"]


def upload_tus(path: Path) -> str:
    from tusclient import client
    up = client.TusClient(f"{API}/tus").uploader(str(path), chunk_size=256 * 1024, metadata={"filename": path.name, "segment": "2"})
    up.upload()
    return up.url.rstrip("/").split("/")[-1]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        clip = Path(tmp) / "demo.mp4"
        make_clip(clip)
        id = (upload_tus if "--tus" in sys.argv else upload_multipart)(clip)
    print(f"job {id}")

    last = None
    while True:
        job = httpx.get(f"{API}/jobs/{id}").json()
        line = f"{job['state']} {(job.get('progress') or {}).get('step', '')}"
        if line != last:
            print(" ", line)
            last = line
        if job["state"] == "failed":
            sys.exit(job["error"])
        if job["state"] == "completed":
            break
        time.sleep(1)

    r = job["result"]
    print(f"{r['playlist']} ({r['encoder']})")
    for q in r["renditions"]:
        print(f"  {q['name']:>6} {q['size']:>9} {q['kbps']}k  vmaf={q.get('vmaf', {}).get('mean')}  xpsnr_y={q.get('xpsnr', {}).get('y')}")


if __name__ == "__main__":
    main()
