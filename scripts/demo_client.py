import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

API_BASE_URL = "http://localhost:8000"


def generate_sample_video(output_path: Path, duration: int = 4) -> None:
    """Generate synthetic MP4 video with test pattern and audio tone."""
    print(f"Generating {duration}s test video...")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=1280x720:rate=25",
        "-f", "lavfi", "-i", f"sine=frequency=880:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-b:v", "2500k",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    print("Video processing demo client")

    try:
        health_resp = httpx.get(f"{API_BASE_URL}/health", timeout=5.0)
        health_resp.raise_for_status()
        print(f"API health: {health_resp.json()}")
    except Exception as exc:
        print(f"API error: {exc}")
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmpdir:
        sample_path = Path(tmpdir) / "sample_720p.mp4"
        generate_sample_video(sample_path, duration=4)

        print("Uploading video...")
        with open(sample_path, "rb") as f:
            upload_resp = httpx.post(
                f"{API_BASE_URL}/api/v1/jobs/upload",
                files={"file": ("sample_720p.mp4", f, "video/mp4")},
                data={
                    "segment_duration": 2,
                    "compute_quality_metrics": "true",
                    "compute_vmaf": "true",
                    "compute_xpsnr": "true",
                },
                timeout=30.0,
            )

        if upload_resp.status_code not in (200, 202):
            print(f"Upload failed: {upload_resp.status_code} {upload_resp.text}")
            sys.exit(1)

        job_id = upload_resp.json()["id"]
        print(f"Job enqueued: {job_id}")

        print("Polling progress:")
        last_step = None
        while True:
            resp = httpx.get(f"{API_BASE_URL}/api/v1/jobs/{job_id}", timeout=5.0)
            status_data = resp.json()
            state = status_data.get("state")
            progress = status_data.get("progress") or {}
            step = progress.get("step") if isinstance(progress, dict) else None
            percent = progress.get("percent", 0) if isinstance(progress, dict) else 0

            step_str = f"[{state}] step={step} ({percent}%)"
            if step_str != last_step:
                print(f"  {step_str}")
                last_step = step_str

            if state == "completed":
                print("Job completed:")
                return_val = status_data.get("return_value") or {}
                print(f"Master playlist: {return_val.get('master_playlist_url')}")

                for r in return_val.get("renditions", []):
                    print(f"  {r['name']}: {r['resolution']} @ {r['bitrate_k']}k")

                qreport = return_val.get("quality_report", {})
                for rname, rinfo in qreport.get("renditions", {}).items():
                    metrics = rinfo.get("metrics", {})
                    vmaf = metrics.get("vmaf", {})
                    xpsnr = metrics.get("xpsnr", {})
                    print(f"  {rname}: VMAF={vmaf.get('mean')} XPSNR_Y={xpsnr.get('y_db')}dB")

                pb_resp = httpx.get(f"{API_BASE_URL}/api/v1/jobs/{job_id}/playback")
                if pb_resp.status_code == 200:
                    print(f"Playback URL: {pb_resp.json().get('presigned_playback_url')}")
                break

            if state == "failed":
                print(f"Job failed: {status_data.get('failed_reason')}")
                break

            time.sleep(1)


if __name__ == "__main__":
    main()
