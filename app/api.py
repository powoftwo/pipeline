import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app import jobs, s3, tus
from app.config import cfg


@asynccontextmanager
async def lifespan(_: FastAPI):
    s3.ensure_buckets()
    yield


app = FastAPI(title="HLS pipeline", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Location", "Upload-Offset", "Upload-Length", *tus.HEADERS],
)
app.include_router(tus.router)


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(Path(__file__).with_name("index.html"))


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/jobs", status_code=202)
async def upload(
    file: UploadFile = File(...),
    segment: int = Form(4, ge=1, le=10),
    hw: bool = Form(False),
    vmaf: bool = Form(True),
    xpsnr: bool = Form(True),
):
    id = str(uuid.uuid4())
    key = f"{id}/{Path(file.filename or 'video.mp4').name}"
    s3.client.upload_fileobj(file.file, cfg.raw_bucket, key, ExtraArgs={"ContentType": file.content_type or "video/mp4"})
    await jobs.enqueue(id, key, {"segment": segment, "hw": hw, "vmaf": vmaf, "xpsnr": xpsnr})
    return {"id": id}


@app.get("/jobs/{id}")
async def job(id: str):
    if not (st := await jobs.status(id)):
        raise HTTPException(404, "job not found")
    return st
