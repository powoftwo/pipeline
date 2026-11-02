import re
import statistics
import subprocess
from pathlib import Path

from ffmpeg_quality_metrics import FfmpegQualityMetrics

from app.probe import Info

XPSNR_RE = re.compile(r"XPSNR\s+y:\s*(\S+)\s+u:\s*(\S+)\s+v:\s*(\S+)\s+\(minimum:\s*([^)\s]+)\)")


def vmaf(dist: Path, ref: Path) -> dict:
    try:
        frames = FfmpegQualityMetrics(str(dist), str(ref)).calculate(["vmaf"]).get("vmaf", [])
        s = [f["vmaf"] for f in frames if "vmaf" in f]
        if not s:
            return {"error": "no vmaf data"}
        return {"mean": round(statistics.mean(s), 2), "min": round(min(s), 2), "hmean": round(statistics.harmonic_mean([max(x, 1e-3) for x in s]), 2)}
    except Exception as e:
        return {"error": str(e)}


def xpsnr(dist: Path, ref: Path, ref_info: Info) -> dict:
    f = f"[0:v]scale={ref_info.width}:{ref_info.height}:flags=bicubic[d];[d][1:v]xpsnr"
    cmd = ["ffmpeg", "-i", str(dist), "-i", str(ref), "-filter_complex", f, "-f", "null", "-"]
    m = XPSNR_RE.search(subprocess.run(cmd, capture_output=True, text=True).stderr)
    if not m:
        return {"error": "xpsnr parse failed"}
    y, u, v, mn = (100.0 if x == "inf" else round(float(x), 2) for x in m.groups())
    return {"y": y, "u": u, "v": v, "min": mn}
