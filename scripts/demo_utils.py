import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for TUS upload demo client."""
    parser = argparse.ArgumentParser(description="TUS resumable upload demo client")
    parser.add_argument(
        "input_file",
        nargs="?",
        default=None,
        help="Path to input video file (optional; defaults to demo.mp4 next to script)",
    )
    parser.add_argument(
        "-t",
        "--duration",
        type=int,
        default=60,
        help="Duration of generated video in seconds (default: 60)",
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
    return parser.parse_args()


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


def prepare_input_video(
    input_file: Optional[str], duration: int = 60, default_dir: Optional[Path] = None
) -> Path:
    """Resolve input video path or generate demo.mp4 with specified duration."""
    if input_file:
        video_path = Path(input_file).expanduser().resolve()
        if not video_path.is_file():
            print(f"Error: input file '{video_path}' does not exist.")
            sys.exit(1)
        print(f"Using input video: {video_path}")
        return video_path

    target_dir = default_dir or Path(__file__).resolve().parent
    video_path = target_dir / "demo.mp4"

    dur = get_video_duration(video_path) if video_path.is_file() else None
    if dur is not None and abs(dur - duration) < 1.0:
        print(f"Using existing demo video: {video_path} ({round(dur)}s)")
    else:
        generate_sample_video(video_path, duration=duration)

    return video_path


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
