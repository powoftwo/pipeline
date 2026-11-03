import asyncio
import json
import logging
import re
import shlex
import statistics
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from ffmpeg_quality_metrics import FfmpegQualityMetrics

from src.ffmpeg.probe import probe_video

logger = logging.getLogger("metrics")


def calculate_vmaf(distorted_path: str | Path, reference_path: str | Path, model_path: Optional[str] = None) -> Dict[str, Any]:
    try:
        ref_meta = probe_video(reference_path)
        fps = ref_meta.fps if ref_meta.fps > 0 else 25.0

        ffqm = FfmpegQualityMetrics(str(distorted_path), str(reference_path))
        results = ffqm.calculate(["vmaf"])
        frames = results.get("vmaf", [])
        if not frames:
            return {"error": "No VMAF data"}
        scores = [f["vmaf"] for f in frames if "vmaf" in f]
        if not scores:
            return {"error": "No VMAF scores"}

        sec_buckets: Dict[int, list] = {}
        for idx, f in enumerate(frames):
            if "vmaf" not in f:
                continue
            frame_num = f.get("n", idx + 1)
            sec = int((frame_num - 1) / fps)
            sec_buckets.setdefault(sec, []).append(f["vmaf"])

        timeline = []
        for s in sorted(sec_buckets.keys()):
            s_vals = sec_buckets[s]
            start_m, start_s = divmod(s, 60)
            end_m, end_s = divmod(s + 1, 60)
            trange = f"{start_m:02d}:{start_s:02d} - {end_m:02d}:{end_s:02d}"
            timeline.append({
                "second": s,
                "time_range": trange,
                "vmaf": round(statistics.mean(s_vals), 2),
            })

        h_mean = len(scores) / sum(1.0 / max(s, 0.001) for s in scores)
        return {
            "mean": round(statistics.mean(scores), 2),
            "min": round(min(scores), 2),
            "max": round(max(scores), 2),
            "harmonic_mean": round(h_mean, 2),
            "frames": len(scores),
            "timeline": timeline,
        }
    except Exception as exc:
        return {"error": str(exc)}


def calculate_xpsnr(distorted_path: str | Path, reference_path: str | Path) -> Dict[str, Any]:
    ref_meta = probe_video(reference_path)
    dist_meta = probe_video(distorted_path)
    fps = ref_meta.fps if ref_meta.fps > 0 else 25.0

    if dist_meta.width != ref_meta.width or dist_meta.height != ref_meta.height:
        scale_filter = f"[0:v]scale={ref_meta.width}:{ref_meta.height}:flags=bicubic[scaled];[scaled][1:v]"
    else:
        scale_filter = "[0:v][1:v]"

    with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as tf:
        stats_path = Path(tf.name)

    try:
        fstr = f"{scale_filter}xpsnr=stats_file='{stats_path}'"
        cmd = ["ffmpeg", "-y", "-i", str(distorted_path), "-i", str(reference_path), "-filter_complex", fstr, "-f", "null", "-"]
        logger.info(f"FFmpeg command (XPSNR): {shlex.join(cmd)}")
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)

        sec_buckets: Dict[int, list] = {}
        if stats_path.exists():
            pat = re.compile(r"n:\s*(\d+)\s+XPSNR\s+[yYrR]:\s*([0-9.]+|inf)")
            with open(stats_path, "r", encoding="utf-8", errors="ignore") as sf:
                for line in sf:
                    m_frame = pat.match(line)
                    if m_frame:
                        frame_num = int(m_frame.group(1))
                        raw_val = m_frame.group(2)
                        val = 100.0 if raw_val == "inf" else float(raw_val)
                        sec = int((frame_num - 1) / fps)
                        sec_buckets.setdefault(sec, []).append(val)

        timeline = []
        for s in sorted(sec_buckets.keys()):
            s_vals = sec_buckets[s]
            start_m, start_s = divmod(s, 60)
            end_m, end_s = divmod(s + 1, 60)
            trange = f"{start_m:02d}:{start_s:02d} - {end_m:02d}:{end_s:02d}"
            timeline.append({
                "second": s,
                "time_range": trange,
                "y_db": round(statistics.mean(s_vals), 2),
            })

        m = re.search(r"XPSNR\s+y:\s*([0-9.]+|inf)\s+u:\s*([0-9.]+|inf)\s+v:\s*([0-9.]+|inf)\s+\(minimum:\s*([0-9.]+|inf)\)", proc.stderr)
        if m:
            parse = lambda v: 100.0 if v == "inf" else round(float(v), 2)
            return {
                "y_db": parse(m.group(1)),
                "u_db": parse(m.group(2)),
                "v_db": parse(m.group(3)),
                "minimum_db": parse(m.group(4)),
                "timeline": timeline,
            }
        elif timeline:
            all_vals = [v for vals in sec_buckets.values() for v in vals]
            avg_y = round(statistics.mean(all_vals), 2) if all_vals else 0.0
            return {
                "y_db": avg_y,
                "timeline": timeline,
            }
        return {"error": "XPSNR parse error"}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        stats_path.unlink(missing_ok=True)


async def evaluate_hls_stream_quality(
    reference_path: str | Path,
    hls_dir: str | Path,
    profiles: list,
    compute_vmaf_flag: bool = True,
    compute_xpsnr_flag: bool = True,
    vmaf_model: Optional[str] = None,
) -> Dict[str, Any]:
    hls_dir = Path(hls_dir)
    report: Dict[str, Any] = {"renditions": {}}
    loop = asyncio.get_running_loop()

    for idx, p in enumerate(profiles):
        m3u8 = hls_dir / f"stream_{idx}" / "index.m3u8"
        if not m3u8.exists():
            continue
        metrics: Dict[str, Any] = {}
        if compute_vmaf_flag:
            metrics["vmaf"] = await loop.run_in_executor(None, calculate_vmaf, m3u8, reference_path, vmaf_model)
        if compute_xpsnr_flag:
            metrics["xpsnr"] = await loop.run_in_executor(None, calculate_xpsnr, m3u8, reference_path)

        report["renditions"][p.name] = {
            "profile": {"name": p.name, "width": p.width, "height": p.height, "bitrate_k": p.video_bitrate_k},
            "metrics": metrics,
        }

    # Cross-rendition table: [time_range] x [rendition]
    grid: Dict[str, Dict[str, Dict[str, float]]] = {}
    time_ranges_ordered: List[str] = []

    for p in profiles:
        rdata = report["renditions"].get(p.name, {})
        metrics = rdata.get("metrics", {})

        vmaf_tl = metrics.get("vmaf", {}).get("timeline", []) if isinstance(metrics.get("vmaf"), dict) else []
        for item in vmaf_tl:
            tr = item["time_range"]
            if tr not in time_ranges_ordered:
                time_ranges_ordered.append(tr)
            grid.setdefault(tr, {}).setdefault(p.name, {})["vmaf"] = item["vmaf"]

        xpsnr_tl = metrics.get("xpsnr", {}).get("timeline", []) if isinstance(metrics.get("xpsnr"), dict) else []
        for item in xpsnr_tl:
            tr = item["time_range"]
            if tr not in time_ranges_ordered:
                time_ranges_ordered.append(tr)
            grid.setdefault(tr, {}).setdefault(p.name, {})["xpsnr"] = item["y_db"]

    time_ranges_ordered.sort()
    report["matrix"] = {
        "time_ranges": time_ranges_ordered,
        "renditions": [p.name for p in profiles if p.name in report["renditions"]],
        "grid": grid,
    }

    with open(hls_dir / "quality_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return report
