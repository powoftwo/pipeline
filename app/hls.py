import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.probe import Info


@dataclass
class Rendition:
    name: str
    width: int
    height: int
    kbps: int
    audio_kbps: int


LADDER = [
    Rendition("1080p", 1920, 1080, 5000, 192),
    Rendition("720p", 1280, 720, 2800, 128),
    Rendition("480p", 854, 480, 1400, 96),
    Rendition("360p", 640, 360, 800, 64),
]
HW_ENCODERS = ["h264_videotoolbox", "h264_nvenc", "h264_vaapi", "h264_qsv"]


def ladder(info: Info) -> list[Rendition]:
    return [r for r in LADDER if r.height <= info.height] or [
        Rendition(f"{info.height}p", info.width, info.height, min(info.kbps or 600, 800), 64)
    ]


def hw_encoder() -> str | None:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return next((e for e in HW_ENCODERS if e in out), None)


def encoder_args(r: Rendition, i: int, gop: int, enc: str) -> list[str]:
    args = [f"-c:v:{i}", enc, f"-b:v:{i}", f"{r.kbps}k", f"-g:v:{i}", str(gop)]
    if enc in ("h264_vaapi", "h264_qsv"):
        return args
    args += [f"-maxrate:v:{i}", f"{int(r.kbps * 1.07)}k", f"-bufsize:v:{i}", f"{int(r.kbps * 1.5)}k"]
    if enc == "h264_nvenc":
        args += ["-preset", "p4"]
    if enc == "libx264":
        args += ["-preset", "veryfast", f"-keyint_min:v:{i}", str(gop), f"-sc_threshold:v:{i}", "0"]
    return args


async def transcode(
    src: Path, out: Path, info: Info, segment: int, hw: bool, on_progress: Callable[[float], None]
) -> tuple[list[Rendition], str]:
    rs = ladder(info)
    gop = max(1, round(info.fps * segment))
    enc = (hw and hw_encoder()) or "libx264"

    split = f"[0:v]split={len(rs)}" + "".join(f"[v{i}]" for i in range(len(rs)))
    scales = [f"[v{i}]scale={r.width}:{r.height}[o{i}]" for i, r in enumerate(rs)]
    cmd = ["ffmpeg", "-y", "-i", str(src), "-filter_complex", ";".join([split, *scales])]

    streams = []
    for i, r in enumerate(rs):
        cmd += ["-map", f"[o{i}]", *encoder_args(r, i, gop, enc)]
        if info.audio:
            cmd += ["-map", "0:a:0", f"-c:a:{i}", "aac", f"-b:a:{i}", f"{r.audio_kbps}k"]
            streams.append(f"v:{i},a:{i}")
        else:
            streams.append(f"v:{i}")

    cmd += [
        "-f", "hls", "-hls_time", str(segment), "-hls_playlist_type", "vod",
        "-hls_flags", "independent_segments",
        "-hls_segment_filename", str(out / "%v" / "%03d.ts"),
        "-master_pl_name", "master.m3u8",
        "-var_stream_map", " ".join(streams),
        "-progress", "pipe:1",
        str(out / "%v" / "index.m3u8"),
    ]

    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    total_us = info.duration * 1e6 or 1
    async for line in proc.stdout:
        k, _, v = line.decode(errors="ignore").strip().partition("=")
        if k == "out_time_us" and v.isdigit():
            on_progress(min(100.0, int(v) / total_us * 100))
    _, err = await proc.communicate()
    if proc.returncode:
        raise RuntimeError(f"ffmpeg failed: {err.decode(errors='ignore')[-2000:]}")
    return rs, enc
