import argparse
import mimetypes
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import httpx
from tusclient import client

API_BASE_URL = "http://localhost:8000"
TUS_ENDPOINT = f"{API_BASE_URL}/api/v1/tus"


def generate_sample_video(output_path: Path, duration: int = 4) -> None:
    """Generate synthetic MP4 video."""
    print(f"Generating {duration}s test video at {output_path}...")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=1280x720:rate=25",
        "-f", "lavfi", "-i", f"sine=frequency=1000:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-b:v", "3000k",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def format_quality_table(qreport: dict) -> Optional[str]:
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


def main():
    parser = argparse.ArgumentParser(description="TUS resumable upload demo client")
    parser.add_argument(
        "input_file",
        nargs="?",
        default=None,
        help="Path to input video file (optional; defaults to demo.mp4 next to script)",
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

    compute_quality = args.vmaf or args.xpsnr
    compute_vmaf = args.vmaf
    compute_xpsnr = args.xpsnr

    print("TUS resumable upload demo client")
    print(
        f"Config: hw={args.hw}, vmaf={compute_vmaf}, xpsnr={compute_xpsnr}"
    )

    if args.input_file:
        video_path = Path(args.input_file).expanduser().resolve()
        if not video_path.is_file():
            print(f"Error: input file '{video_path}' does not exist.")
            sys.exit(1)
        print(f"Using input video: {video_path}")
    else:
        video_path = Path(__file__).resolve().parent / "demo.mp4"
        if video_path.is_file():
            print(f"Using existing demo video: {video_path}")
        else:
            generate_sample_video(video_path, duration=4)

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

    total_size = video_path.stat().st_size
    filetype = mimetypes.guess_type(video_path.name)[0] or "video/mp4"

    print(f"Starting TUS upload ({total_size} bytes)...")
    tus_c = client.TusClient(TUS_ENDPOINT)
    chunk_size = 256 * 1024

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

    uploader = tus_c.uploader(
        str(video_path),
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
            elif compute_quality:
                print("Quality report: no quality data available")

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
