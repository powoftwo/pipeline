import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class VideoMetadata:
    width: int
    height: int
    duration_seconds: float
    bitrate_kbps: int
    video_codec: str
    audio_codec: Optional[str]
    fps: float
    raw_info: Dict[str, Any]


def probe_video(file_path: str | Path) -> VideoMetadata:
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(file_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    data = json.loads(result.stdout)
    vstream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    astream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)

    if not vstream:
        raise ValueError(f"No video stream in {file_path}")

    width = int(vstream.get("width", 0))
    height = int(vstream.get("height", 0))

    dur_str = data.get("format", {}).get("duration") or vstream.get("duration") or "0.0"
    duration = float(dur_str)

    b_str = data.get("format", {}).get("bit_rate") or vstream.get("bit_rate") or "0"
    bitrate_k = int(int(b_str) / 1000) if b_str.isdigit() else 0

    fps_str = vstream.get("r_frame_rate", "30/1")
    if "/" in fps_str:
        num, den = fps_str.split("/")
        fps = float(num) / float(den) if float(den) != 0 else 30.0
    else:
        fps = float(fps_str) if fps_str else 30.0

    return VideoMetadata(
        width=width,
        height=height,
        duration_seconds=duration,
        bitrate_kbps=bitrate_k,
        video_codec=vstream.get("codec_name", "unknown"),
        audio_codec=astream.get("codec_name") if astream else None,
        fps=fps,
        raw_info=data,
    )
