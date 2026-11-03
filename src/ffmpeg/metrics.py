import asyncio
import json
import logging
import re
import shlex
import statistics
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from ffmpeg_quality_metrics import FfmpegQualityMetrics

from src.ffmpeg.probe import probe_video

logger = logging.getLogger("metrics")


def calculate_vmaf(distorted_path: str | Path, reference_path: str | Path, model_path: Optional[str] = None) -> Dict[str, Any]:
    try:
        ffqm = FfmpegQualityMetrics(str(distorted_path), str(reference_path))
        results = ffqm.calculate(["vmaf"])
        frames = results.get("vmaf", [])
        if not frames:
            return {"error": "No VMAF data"}
        scores = [f["vmaf"] for f in frames if "vmaf" in f]
        if not scores:
            return {"error": "No VMAF scores"}
        h_mean = len(scores) / sum(1.0 / max(s, 0.001) for s in scores)
        return {
            "mean": round(statistics.mean(scores), 2),
            "min": round(min(scores), 2),
            "max": round(max(scores), 2),
            "harmonic_mean": round(h_mean, 2),
            "frames": len(scores),
        }
    except Exception as exc:
        return {"error": str(exc)}


def calculate_xpsnr(distorted_path: str | Path, reference_path: str | Path) -> Dict[str, Any]:
    ref_meta = probe_video(reference_path)
    dist_meta = probe_video(distorted_path)

    if dist_meta.width != ref_meta.width or dist_meta.height != ref_meta.height:
        fstr = f"[0:v]scale={ref_meta.width}:{ref_meta.height}:flags=bicubic[scaled];[scaled][1:v]xpsnr"
    else:
        fstr = "[0:v][1:v]xpsnr"

    cmd = ["ffmpeg", "-y", "-i", str(distorted_path), "-i", str(reference_path), "-filter_complex", fstr, "-f", "null", "-"]
    logger.info(f"FFmpeg command (XPSNR): {shlex.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        m = re.search(r"XPSNR\s+y:\s*([0-9.]+|inf)\s+u:\s*([0-9.]+|inf)\s+v:\s*([0-9.]+|inf)\s+\(minimum:\s*([0-9.]+|inf)\)", proc.stderr)
        if m:
            parse = lambda v: 100.0 if v == "inf" else round(float(v), 2)
            return {"y_db": parse(m.group(1)), "u_db": parse(m.group(2)), "v_db": parse(m.group(3)), "minimum_db": parse(m.group(4))}
        return {"error": "XPSNR parse error"}
    except Exception as exc:
        return {"error": str(exc)}


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

    with open(hls_dir / "quality_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return report
