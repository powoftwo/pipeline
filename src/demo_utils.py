import argparse
import mimetypes
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from tqdm import tqdm
from tusclient import client

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = PROJECT_ROOT / "samples"

API_BASE_URL = "http://localhost:8000"
TUS_ENDPOINT = f"{API_BASE_URL}/api/v1/tus"


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for TUS upload demo client."""
    parser = argparse.ArgumentParser(description="TUS resumable upload demo client")
    parser.add_argument(
        "input_file",
        nargs="?",
        default=None,
        help="Path to input video file (optional; defaults to samples/demo.mp4)",
    )
    parser.add_argument(
        "-t",
        "--duration",
        type=int,
        default=None,
        help="Duration in seconds (default: 60 for demo.mp4, or trims input_file)",
    )
    parser.add_argument(
        "--hw",
        action="store_true",
        default=False,
        help="Enable hardware acceleration",
    )
    parser.add_argument(
        "--vmaf",
        action="store_true",
        default=False,
        help="Enable VMAF quality calculation",
    )
    parser.add_argument(
        "--xpsnr",
        action="store_true",
        default=False,
        help="Enable XPSNR quality calculation",
    )
    args = parser.parse_args()
    dur_label = f"{args.duration}s" if args.duration else ("60s (default)" if not args.input_file else "full")
    print("TUS resumable upload demo client")
    print(f"Config: duration={dur_label}, hw={args.hw}, vmaf={args.vmaf}, xpsnr={args.xpsnr}")
    return args


def get_video_duration(video_path: Path) -> Optional[float]:
    """Extract video duration using ffprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(res.stdout.strip())
    except Exception:
        return None


def generate_sample_video(output_path: Path, duration: int = 60) -> None:
    """Generate synthetic test MP4 video via ffmpeg testsrc."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Generating {duration}s test video at {output_path}...")
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration}:size=1920x1080:rate=25",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=1000:duration={duration}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-b:v",
        "3000k",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def trim_video(source_path: Path, duration: int, output_dir: Optional[Path] = None) -> Path:
    """Trim first N seconds of video using stream copy (with fallback to fast re-encode)."""
    target_dir = output_dir or SAMPLES_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    out_name = f"{source_path.stem}_trim_{duration}s{source_path.suffix}"
    out_path = target_dir / out_name

    cached_dur = get_video_duration(out_path) if out_path.is_file() else None
    if cached_dur is not None and abs(cached_dur - duration) < 1.0:
        print(f"Using existing trimmed video: {out_path} ({round(cached_dur)}s)")
        return out_path

    print(f"Trimming {duration}s from {source_path.name} -> {out_name}...")
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        "0",
        "-i",
        str(source_path),
        "-t",
        str(duration),
        "-c",
        "copy",
        str(out_path),
    ]
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        cmd_fallback = [
            "ffmpeg",
            "-y",
            "-ss",
            "0",
            "-i",
            str(source_path),
            "-t",
            str(duration),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            str(out_path),
        ]
        subprocess.run(cmd_fallback, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return out_path


def prepare_input_video(
    input_file: Optional[str], duration: Optional[int] = None, default_dir: Optional[Path] = None
) -> Path:
    """Resolve input video path or generate samples/demo.mp4 with specified duration.

    - If input_file is given and duration is given: trims input_file to duration seconds in samples/.
    - If input_file is given and duration is None: uses full input_file as-is.
    - If input_file is not given: generates or reuses samples/demo.mp4 with duration (default 60s).
    """
    target_dir = default_dir or SAMPLES_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    if input_file:
        video_path = Path(input_file).expanduser().resolve()
        if not video_path.is_file():
            print(f"Error: input file '{video_path}' does not exist.")
            sys.exit(1)

        if duration is not None:
            source_dur = get_video_duration(video_path)
            if source_dur is not None and source_dur <= duration:
                print(f"Using input video: {video_path} ({round(source_dur)}s <= requested {duration}s)")
                return video_path
            return trim_video(video_path, duration=duration, output_dir=target_dir)

        print(f"Using input video: {video_path}")
        return video_path

    target_dur = duration or 60
    video_path = target_dir / "demo.mp4"

    dur = get_video_duration(video_path) if video_path.is_file() else None
    if dur is not None and abs(dur - target_dur) < 1.0:
        print(f"Using existing demo video: {video_path} ({round(dur)}s)")
    else:
        generate_sample_video(video_path, duration=target_dur)

    return video_path


def check_api_health(api_base_url: str = API_BASE_URL, tus_endpoint: str = TUS_ENDPOINT) -> None:
    """Verify API availability and query TUS protocol capabilities."""
    try:
        health_resp = httpx.get(f"{api_base_url}/health", timeout=5.0)
        health_resp.raise_for_status()
        print(f"API health: {health_resp.json()}")
    except Exception as exc:
        print(f"API error: {exc}")
        sys.exit(1)

    try:
        opt_resp = httpx.options(tus_endpoint, timeout=5.0)
        max_gb = int(opt_resp.headers.get("Tus-Max-Size", 0)) / (1024**3)
        print(f"TUS options: version={opt_resp.headers.get('Tus-Version')} max_size={max_gb:.1f}GB")
    except Exception as exc:
        print(f"TUS options warning: {exc}")


def upload_video_via_tus(
    endpoint: str,
    video_path: Path,
    args: argparse.Namespace,
    chunk_size: int = 2 * 1024 * 1024,
) -> str:
    """Upload video chunk-by-chunk via TUS 1.0.0 protocol with live progress bar."""
    total_size = video_path.stat().st_size
    filetype = mimetypes.guess_type(video_path.name)[0] or "video/mp4"
    compute_quality = bool(args.vmaf or args.xpsnr)

    metadata = {
        "filename": video_path.name,
        "filetype": filetype,
        "segment_duration": "2",
        "compute_quality_metrics": "true" if compute_quality else "false",
        "compute_vmaf": "true" if args.vmaf else "false",
        "compute_xpsnr": "true" if args.xpsnr else "false",
        "hw_accel": "true" if args.hw else "false",
        "auto_process": "true",
    }

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

    upload_id = uploader.url.rstrip("/").split("/")[-1]
    return upload_id


def get_job_id_by_upload(endpoint: str, upload_id: str) -> Optional[str]:
    """Retrieve BullMQ job ID assigned to completed TUS upload session."""
    time.sleep(0.5)
    job_id = None
    for _ in range(5):
        try:
            resp = httpx.get(f"{endpoint}/{upload_id}", timeout=5.0)
            if resp.status_code == 200:
                job_id = resp.json().get("job_id")
                if job_id:
                    break
        except Exception:
            pass
        time.sleep(0.5)

    print(f"BullMQ job: {job_id}")
    return job_id


def poll_job_progress(api_base_url: str, job_id: str) -> Dict[str, Any]:
    """Poll BullMQ job status with live tqdm progress bar until completion."""
    status_data: Dict[str, Any] = {}

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
                break

            time.sleep(0.5)

    return status_data


def print_job_summary(status_data: Dict[str, Any], api_base_url: str = API_BASE_URL, compute_quality: bool = False) -> None:
    """Print job completion report, profiles, QC tables and playback URL."""
    if status_data.get("state") != "completed":
        return

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

    job_id = status_data.get("id") or return_val.get("job_id")
    if job_id:
        try:
            pb_resp = httpx.get(f"{api_base_url}/api/v1/jobs/{job_id}/playback", timeout=5.0)
            if pb_resp.status_code == 200:
                print(f"Playback URL: {pb_resp.json().get('presigned_playback_url')}")
        except Exception:
            pass


def format_quality_table(qreport: dict) -> Optional[str]:
    """Build ASCII matrix table for cross-rendition VMAF & XPSNR quality metrics."""
    matrix = qreport.get("matrix") or {}
    time_ranges = matrix.get("time_ranges") or []
    renditions = matrix.get("renditions") or []
    grid = matrix.get("grid") or {}

    if not time_ranges or not renditions:
        return None

    has_vmaf = any("vmaf" in grid.get(tr, {}).get(r, {}) for tr in time_ranges for r in renditions)
    has_xpsnr = any("xpsnr" in grid.get(tr, {}).get(r, {}) for tr in time_ranges for r in renditions)

    if not has_vmaf and not has_xpsnr:
        return None

    headers = ["Time"] + renditions
    rows = []
    for tr in time_ranges:
        row = [tr]
        for r in renditions:
            cell = grid.get(tr, {}).get(r, {})
            parts = []
            if has_vmaf and "vmaf" in cell:
                v = cell["vmaf"]
                parts.append(f"V:{v:.1f}" if has_xpsnr else f"{v:.1f}")
            if has_xpsnr and "xpsnr" in cell:
                x = cell["xpsnr"]
                parts.append(f"X:{x:.1f}dB" if has_vmaf else f"{x:.1f} dB")
            row.append("  ".join(parts) if parts else "-")
        rows.append(row)

    avg_row = ["Average"]
    r_averages = qreport.get("renditions", {})
    for r in renditions:
        m = r_averages.get(r, {}).get("metrics", {})
        parts = []
        if has_vmaf and "vmaf" in m and "mean" in m["vmaf"]:
            v = m["vmaf"]["mean"]
            parts.append(f"V:{v:.1f}" if has_xpsnr else f"{v:.1f}")
        if has_xpsnr and "xpsnr" in m and "y_db" in m["xpsnr"]:
            x = m["xpsnr"]["y_db"]
            parts.append(f"X:{x:.1f}dB" if has_vmaf else f"{x:.1f} dB")
        avg_row.append("  ".join(parts) if parts else "-")

    col_widths = [len(h) for h in headers]
    for r in rows + [avg_row]:
        for i, val in enumerate(r):
            col_widths[i] = max(col_widths[i], len(val))

    def make_line(sep="+", pad="-"):
        return sep + sep.join(pad * (w + 2) for w in col_widths) + sep

    def format_row(r):
        return "| " + " | ".join(val.ljust(col_widths[i]) for i, val in enumerate(r)) + " |"

    res = [
        "Quality Metrics Table [Time] x [Resolution]:",
        make_line(),
        format_row(headers),
        make_line("+", "="),
    ]
    for r in rows:
        res.append(format_row(r))
    res.append(make_line())
    res.append(format_row(avg_row))
    res.append(make_line())
    return "\n".join(res)


def print_quality_report(qreport: dict, compute_quality: bool) -> None:
    """Print ASCII quality table or fallback per-rendition summary."""
    tbl = format_quality_table(qreport)
    if tbl:
        print(f"\n{tbl}\n")

    renditions_qc = qreport.get("renditions", {})
    if renditions_qc and not tbl:
        print("Quality metrics:")
        for rname, rinfo in renditions_qc.items():
            metrics = rinfo.get("metrics", {})
            parts = []
            if "vmaf" in metrics:
                vmaf_data = metrics.get("vmaf") or {}
                if "mean" in vmaf_data:
                    parts.append(f"VMAF={vmaf_data['mean']}")
                elif "error" in vmaf_data:
                    parts.append(f"VMAF error: {vmaf_data['error']}")
            if "xpsnr" in metrics:
                xpsnr_data = metrics.get("xpsnr") or {}
                if "y_db" in xpsnr_data:
                    parts.append(f"XPSNR_Y={xpsnr_data['y_db']}dB")
                elif "error" in xpsnr_data:
                    parts.append(f"XPSNR error: {xpsnr_data['error']}")
            summary = " ".join(parts) if parts else "no metrics"
            print(f"  {rname}: {summary}")
    elif compute_quality and not tbl:
        print("Quality report: no quality data available")
