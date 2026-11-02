import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Info:
    width: int
    height: int
    duration: float
    fps: float
    kbps: int
    audio: bool


def probe(path: Path) -> Info:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        raise ValueError(f"no video stream: {path}")
    fmt = data.get("format", {})
    num, _, den = v.get("r_frame_rate", "30/1").partition("/")
    return Info(
        width=int(v["width"]),
        height=int(v["height"]),
        duration=float(fmt.get("duration") or v.get("duration") or 0),
        fps=float(num) / float(den or 1) if float(den or 1) else 30.0,
        kbps=int(fmt.get("bit_rate") or 0) // 1000,
        audio=any(s.get("codec_type") == "audio" for s in streams),
    )
