"""Minimal tus 1.0.0 server (creation, creation-with-upload, termination).
On completion the file goes to S3 and a job is enqueued with id = upload id."""
import asyncio
import base64
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response

from app import jobs, s3
from app.config import cfg

router = APIRouter(prefix="/tus", tags=["tus"])
VERSION = "1.0.0"
HEADERS = {
    "Tus-Resumable": VERSION,
    "Tus-Version": VERSION,
    "Tus-Extension": "creation,creation-with-upload,termination",
    "Tus-Max-Size": str(cfg.tus_max),
}
DIR = cfg.tmp_dir / "tus"
DIR.mkdir(parents=True, exist_ok=True)


def fail(code: int, msg: str, **extra: str):
    raise HTTPException(code, msg, headers={"Tus-Resumable": VERSION, **extra})


def parse_meta(header: str | None) -> dict[str, str]:
    meta = {}
    for item in (header or "").split(","):
        k, _, v = item.strip().partition(" ")
        if k:
            meta[k] = base64.b64decode(v).decode(errors="replace") if v else ""
    return meta


def flag(meta: dict, key: str, default: bool) -> bool:
    return meta.get(key, str(default)).lower() in ("true", "1", "yes")


def paths(id: str) -> tuple[Path, Path]:
    return DIR / f"{id}.part", DIR / f"{id}.json"


def load(id: str) -> tuple[Path, dict]:
    part, info = paths(id)
    if not info.exists():
        fail(404, "upload not found")
    return part, json.loads(info.read_text())


async def write(id: str, req: Request) -> int:
    part, meta = load(id)
    with part.open("ab") as f:
        async for chunk in req.stream():
            f.write(chunk)
    size = part.stat().st_size
    if size > meta["length"]:
        fail(400, "upload exceeds Upload-Length")
    if size == meta["length"]:
        await finish(id, part, meta)
    return size


async def finish(id: str, part: Path, meta: dict) -> None:
    m = meta["meta"]
    name = Path(m.get("filename") or "video.mp4").name
    key = f"{id}/{name}"
    await asyncio.to_thread(s3.put, part, cfg.raw_bucket, key, m.get("filetype") or "video/mp4")
    await jobs.enqueue(id, key, {
        "segment": int(m.get("segment", 4)),
        "hw": flag(m, "hw", False),
        "vmaf": flag(m, "vmaf", True),
        "xpsnr": flag(m, "xpsnr", True),
    })
    for p in paths(id):
        p.unlink(missing_ok=True)


@router.options("")
@router.options("/{id}")
async def options():
    return Response(status_code=204, headers=HEADERS)


@router.post("")
async def create(req: Request):
    length = req.headers.get("Upload-Length", "")
    if not length.isdigit():
        fail(400, "bad Upload-Length")
    if int(length) > cfg.tus_max:
        fail(413, "too large")

    id = str(uuid.uuid4())
    part, info = paths(id)
    part.touch()
    info.write_text(json.dumps({"length": int(length), "meta": parse_meta(req.headers.get("Upload-Metadata"))}))

    offset = 0
    if req.headers.get("Content-Type") == "application/offset+octet-stream":
        offset = await write(id, req)
    return Response(status_code=201, headers={**HEADERS, "Location": f"{req.url}/{id}", "Upload-Offset": str(offset)})


@router.head("/{id}")
async def head(id: str):
    part, meta = load(id)
    return Response(headers={
        **HEADERS,
        "Upload-Offset": str(part.stat().st_size),
        "Upload-Length": str(meta["length"]),
        "Cache-Control": "no-store",
    })


@router.patch("/{id}")
async def patch(id: str, req: Request):
    part, _ = load(id)
    if not req.headers.get("Content-Type", "").startswith("application/offset+octet-stream"):
        fail(415, "Content-Type must be application/offset+octet-stream")
    offset = str(part.stat().st_size)
    if req.headers.get("Upload-Offset") != offset:
        fail(409, "offset mismatch", **{"Upload-Offset": offset})
    return Response(status_code=204, headers={**HEADERS, "Upload-Offset": str(await write(id, req))})


@router.delete("/{id}")
async def delete(id: str):
    load(id)
    for p in paths(id):
        p.unlink(missing_ok=True)
    return Response(status_code=204, headers=HEADERS)
