import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from src.ffmpeg.probe import VideoMetadata, probe_video


@dataclass
class RenditionProfile:
    name: str
    width: int
    height: int
    video_bitrate_k: int
    audio_bitrate_k: int


STANDARD_PROFILES: List[RenditionProfile] = [
    RenditionProfile("1080p", 1920, 1080, 5000, 192),
    RenditionProfile("720p", 1280, 720, 2800, 128),
    RenditionProfile("480p", 854, 480, 1400, 96),
    RenditionProfile("360p", 640, 360, 800, 64),
]


def select_profiles(metadata: VideoMetadata) -> List[RenditionProfile]:
    sel = [p for p in STANDARD_PROFILES if p.height <= metadata.height]
    if not sel:
        sel = [RenditionProfile(f"{metadata.height}p", metadata.width, metadata.height, min(metadata.bitrate_kbps or 600, 800), 64)]
    return sel


async def generate_adaptive_hls(
    input_path: str | Path,
    output_dir: str | Path,
    segment_duration: int = 4,
    progress_callback: Optional[Callable[[float], None]] = None,
) -> List[RenditionProfile]:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    metadata = probe_video(input_path)
    profiles = select_profiles(metadata)
    gop_size = max(1, int(round(metadata.fps * segment_duration)))

    for idx in range(len(profiles)):
        (output_dir / f"stream_{idx}").mkdir(parents=True, exist_ok=True)

    split_outputs = "".join(f"[v{i}]" for i in range(len(profiles)))
    fparts = [f"[0:v]split={len(profiles)}{split_outputs}"]
    for i, p in enumerate(profiles):
        fparts.append(f"[v{i}]scale=w={p.width}:h={p.height}[v{i}out]")

    cmd = ["ffmpeg", "-y", "-i", str(input_path), "-filter_complex", ";".join(fparts)]

    vsm = []
    for i, p in enumerate(profiles):
        cmd.extend([
            "-map", f"[v{i}out]", f"-c:v:{i}", "libx264",
            f"-b:v:{i}", f"{p.video_bitrate_k}k",
            f"-maxrate:v:{i}", f"{int(p.video_bitrate_k * 1.07)}k",
            f"-bufsize:v:{i}", f"{int(p.video_bitrate_k * 1.5)}k",
            "-preset", "veryfast",
            f"-g:v:{i}", str(gop_size), f"-keyint_min:v:{i}", str(gop_size), f"-sc_threshold:v:{i}", "0",
        ])
        if metadata.audio_codec:
            cmd.extend(["-map", "0:a:0", f"-c:a:{i}", "aac", f"-b:a:{i}", f"{p.audio_bitrate_k}k"])
            vsm.append(f"v:{i},a:{i}")
        else:
            vsm.append(f"v:{i}")

    cmd.extend([
        "-f", "hls", "-hls_time", str(segment_duration), "-hls_playlist_type", "vod",
        "-hls_flags", "independent_segments",
        "-hls_segment_filename", str(output_dir / "stream_%v" / "data%03d.ts"),
        "-master_pl_name", "master.m3u8",
        "-var_stream_map", " ".join(vsm),
        "-progress", "pipe:1",
        str(output_dir / "stream_%v" / "index.m3u8"),
    ])

    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    total_us = metadata.duration_seconds * 1_000_000 if metadata.duration_seconds > 0 else 1

    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        text = line.decode(errors="ignore").strip()
        if text.startswith("out_time_us="):
            val = text.split("=")[-1]
            if val.isdigit() and progress_callback:
                progress_callback(min(100.0, max(0.0, (float(val) / total_us) * 100.0)))

    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"HLS failed ({proc.returncode}): {stderr.decode(errors='ignore')}")

    return profiles
