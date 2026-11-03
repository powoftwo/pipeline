import mimetypes
import sys
import time
from pathlib import Path

import httpx
from tqdm import tqdm
from tusclient import client

try:
    from scripts.demo_utils import parse_args, prepare_input_video, print_quality_report
except ImportError:
    from demo_utils import parse_args, prepare_input_video, print_quality_report

API_BASE_URL = "http://localhost:8000"
TUS_ENDPOINT = f"{API_BASE_URL}/api/v1/tus"


def upload_video_via_tus(
    endpoint: str, video_path: Path, metadata: dict, chunk_size: int = 2 * 1024 * 1024
) -> str:
    """Upload video file chunk-by-chunk via TUS protocol and return upload URL."""
    total_size = video_path.stat().st_size

    tus_c = client.TusClient(endpoint)
    uploader = tus_c.uploader(
        str(video_path),
        chunk_size=chunk_size,
        metadata=metadata,
        retries=5,
        retry_delay=1,
    )

    max_retries = 10
    retry_count = 0

    with tqdm(
        total=total_size,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        desc="Upload",
        dynamic_ncols=True,
    ) as pbar:
        while uploader.offset < total_size:
            prev_offset = uploader.offset
            try:
                uploader.upload_chunk()
                delta = uploader.offset - prev_offset
                if delta > 0:
                    pbar.update(delta)
                retry_count = 0
                time.sleep(0.01)
            except Exception as exc:
                retry_count += 1
                if retry_count > max_retries:
                    raise
                pbar.write(f"  [warn] upload interrupted ({exc}), resuming... (retry {retry_count}/{max_retries})")
                time.sleep(1)
                try:
                    new_offset = uploader.get_offset()
                    delta = new_offset - uploader.offset
                    if delta > 0:
                        pbar.update(delta)
                    uploader.offset = new_offset
                except Exception:
                    pass

    return uploader.url


def poll_job_status(api_base_url: str, job_id: str, compute_quality: bool) -> None:
    """Poll BullMQ job status until completion or failure."""
    status_data = {}

    with tqdm(
        total=100.0,
        desc="Processing",
        unit="%",
        dynamic_ncols=True,
    ) as pbar:
        pbar.set_postfix(step="queued")
        last_pct = 0.0

        while True:
            try:
                resp = httpx.get(f"{api_base_url}/api/v1/jobs/{job_id}", timeout=5.0)
                if resp.status_code != 200:
                    time.sleep(1)
                    continue
                status_data = resp.json()
            except Exception:
                time.sleep(1)
                continue

            state = status_data.get("state")
            progress = status_data.get("progress") or {}
            step = progress.get("step") if isinstance(progress, dict) else (state or "waiting")
            percent = float(progress.get("percent", 0) if isinstance(progress, dict) else 0)

            if percent > last_pct:
                pbar.update(percent - last_pct)
                last_pct = percent
            pbar.set_postfix(step=step)

            if state == "completed":
                if last_pct < 100.0:
                    pbar.update(100.0 - last_pct)
                pbar.set_postfix(step="completed")
                pbar.refresh()
                break

            if state == "failed":
                pbar.set_postfix(step="failed")
                pbar.refresh()
                print(f"\nJob failed: {status_data.get('failed_reason')}")
                return

            time.sleep(0.5)

    print("\nJob completed:")
    return_val = status_data.get("return_value") or {}
    print(f"Master playlist: {return_val.get('master_playlist_url')}")

    encoder = return_val.get("encoder")
    if encoder:
        hw_used = return_val.get("hw_accel", False)
        print(f"Encoder: {encoder} (hw_accel={hw_used})")

    timings = return_val.get("stage_timings")
    if timings:
        t_parts = [f"transcode: {timings.get('transcode_seconds')}s"]
        if timings.get("quality_seconds") is not None:
            t_parts.append(f"quality: {timings.get('quality_seconds')}s")
        t_parts.append(f"total: {timings.get('total_seconds')}s")
        print(f"Timings: {', '.join(t_parts)}")

    for r in return_val.get("renditions", []):
        print(f"  {r['name']}: {r['resolution']} @ {r['bitrate_k']}k")

    qreport = return_val.get("quality_report", {})
    print_quality_report(qreport, compute_quality=compute_quality)

    try:
        pb_resp = httpx.get(f"{api_base_url}/api/v1/jobs/{job_id}/playback", timeout=5.0)
        if pb_resp.status_code == 200:
            print(f"Playback URL: {pb_resp.json().get('presigned_playback_url')}")
    except Exception:
        pass


def main():
    args = parse_args()

    compute_quality = args.vmaf or args.xpsnr
    compute_vmaf = args.vmaf
    compute_xpsnr = args.xpsnr

    dur_label = f"{args.duration}s" if args.duration else ("60s (default)" if not args.input_file else "full")
    print("TUS resumable upload demo client")
    print(
        f"Config: duration={dur_label}, hw={args.hw}, vmaf={compute_vmaf}, xpsnr={compute_xpsnr}"
    )

    video_path = prepare_input_video(
        input_file=args.input_file,
        duration=args.duration,
        default_dir=Path(__file__).resolve().parent,
    )

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

    filetype = mimetypes.guess_type(video_path.name)[0] or "video/mp4"
    metadata = {
        "filename": video_path.name,
        "filetype": filetype,
        "segment_duration": "2",
        "compute_quality_metrics": "true" if compute_quality else "false",
        "compute_vmaf": "true" if compute_vmaf else "false",
        "compute_xpsnr": "true" if compute_xpsnr else "false",
        "hw_accel": "true" if args.hw else "false",
        "auto_process": "true",
    }

    upload_url = upload_video_via_tus(TUS_ENDPOINT, video_path, metadata)
    upload_id = upload_url.rstrip("/").split("/")[-1]

    time.sleep(0.5)
    job_id = None
    for _ in range(5):
        try:
            resp = httpx.get(f"{TUS_ENDPOINT}/{upload_id}", timeout=5.0)
            if resp.status_code == 200:
                job_id = resp.json().get("job_id")
                if job_id:
                    break
        except Exception:
            pass
        time.sleep(0.5)

    print(f"BullMQ job: {job_id}")

    if job_id:
        poll_job_status(API_BASE_URL, job_id, compute_quality=compute_quality)


if __name__ == "__main__":
    main()
