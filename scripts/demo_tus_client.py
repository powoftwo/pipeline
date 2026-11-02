import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from tusclient import client

API_BASE_URL = "http://localhost:8000"
TUS_ENDPOINT = f"{API_BASE_URL}/api/v1/tus"


def generate_sample_video(output_path: Path, duration: int = 4) -> None:
    """Generate synthetic MP4 video."""
    print(f"Generating {duration}s test video...")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=1280x720:rate=25",
        "-f", "lavfi", "-i", f"sine=frequency=1000:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-b:v", "3000k",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    print("TUS resumable upload demo client")

    try:
        health_resp = httpx.get(f"{API_BASE_URL}/health", timeout=5.0)
        health_resp.raise_for_status()
        print(f"API health: {health_resp.json()}")
    except Exception as exc:
        print(f"API error: {exc}")
        sys.exit(1)

    try:
        opt_resp = httpx.options(TUS_ENDPOINT, timeout=5.0)
        max_gb = int(opt_resp.headers.get("Tus-Max-Size", 0)) / (1024**3)
        print(f"TUS options: version={opt_resp.headers.get('Tus-Version')} max_size={max_gb:.1f}GB")
    except Exception as exc:
        print(f"TUS options warning: {exc}")

    with tempfile.TemporaryDirectory() as tmpdir:
        sample_path = Path(tmpdir) / "tus_sample_720p.mp4"
        generate_sample_video(sample_path, duration=4)
        total_size = sample_path.stat().st_size

        print(f"Starting TUS upload ({total_size} bytes)...")
        tus_c = client.TusClient(TUS_ENDPOINT)
        chunk_size = 256 * 1024

        metadata = {
            "filename": "tus_sample_720p.mp4",
            "filetype": "video/mp4",
            "segment_duration": "2",
            "compute_quality_metrics": "true",
            "compute_vmaf": "true",
            "compute_xpsnr": "true",
            "auto_process": "true",
        }

        uploader = tus_c.uploader(
            str(sample_path),
            chunk_size=chunk_size,
            metadata=metadata,
        )

        chunk_idx = 1
        while uploader.offset < total_size:
            uploader.upload_chunk()
            pct = (uploader.offset / total_size) * 100.0
            print(f"  chunk {chunk_idx:02d}: {uploader.offset}/{total_size} bytes ({pct:.1f}%)")
            chunk_idx += 1
            time.sleep(0.05)

        upload_id = uploader.url.rstrip("/").split("/")[-1]
        print(f"Upload complete: {uploader.url}")

        time.sleep(0.5)
        tus_info = httpx.get(f"{TUS_ENDPOINT}/{upload_id}").json()
        job_id = tus_info.get("job_id")
        print(f"BullMQ job: {job_id}")

        if not job_id:
            return

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
