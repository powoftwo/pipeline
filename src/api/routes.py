import uuid
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from src.api.schemas import (
    JobCreateRequest,
    JobStatusResponse,
    PlaybackResponse,
    PresignedUploadRequest,
    PresignedUploadResponse,
)
from src.config import settings
from src.queue import (
    cancel_job,
    delete_job,
    enqueue_video_job,
    get_job_by_id,
    get_job_status_details,
    list_jobs_details,
)
from src.s3 import s3_storage

router = APIRouter(prefix="/api/v1", tags=["Jobs"])


@router.post("/jobs/upload", response_model=JobStatusResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_video_and_process(
    file: UploadFile = File(...),
    segment_duration: int = Form(4, ge=1, le=10),
    compute_quality_metrics: bool = Form(True),
    compute_vmaf: bool = Form(True),
    compute_xpsnr: bool = Form(True),
    hw_accel: bool = Form(False),
):
    uid = str(uuid.uuid4())
    filename = Path(file.filename or "video.mp4").name
    s3_key = f"uploads/{uid}/{filename}"

    temp_file = settings.temp_path / f"up_{uid}_{filename}"
    try:
        with open(temp_file, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                buffer.write(chunk)
        s3_storage.upload_file(temp_file, settings.S3_BUCKET_RAW, s3_key, file.content_type)
    finally:
        temp_file.unlink(missing_ok=True)

    job = await enqueue_video_job(
        s3_key=s3_key,
        bucket=settings.S3_BUCKET_RAW,
        options={
            "segment_duration": segment_duration,
            "compute_quality_metrics": compute_quality_metrics,
            "compute_vmaf": compute_vmaf,
            "compute_xpsnr": compute_xpsnr,
            "hw_accel": hw_accel,
        },
        job_id=uid,
    )

    return JobStatusResponse(id=str(job.id), name=job.name, state="waiting", progress={"step": "enqueued", "percent": 0})


@router.post("/jobs/presigned", response_model=PresignedUploadResponse)
async def get_presigned_upload_url(payload: PresignedUploadRequest):
    uid = str(uuid.uuid4())
    filename = Path(payload.file_name).name
    s3_key = f"uploads/{uid}/{filename}"

    upload_url = s3_storage.generate_presigned_upload_url(
        bucket=settings.S3_BUCKET_RAW,
        s3_key=s3_key,
        expires_in=3600,
    )
    return PresignedUploadResponse(upload_url=upload_url, s3_bucket=settings.S3_BUCKET_RAW, s3_key=s3_key)


@router.post("/jobs", response_model=JobStatusResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_job_from_s3(payload: JobCreateRequest):
    job = await enqueue_video_job(
        s3_key=payload.s3_key,
        bucket=payload.s3_bucket or settings.S3_BUCKET_RAW,
        options={
            "segment_duration": payload.segment_duration,
            "compute_quality_metrics": payload.compute_quality_metrics,
            "compute_vmaf": payload.compute_vmaf,
            "compute_xpsnr": payload.compute_xpsnr,
            "hw_accel": bool(payload.hw_accel),
        },
    )
    return JobStatusResponse(id=str(job.id), name=job.name, state="waiting", progress={"step": "enqueued", "percent": 0})


@router.get("/jobs", response_model=List[JobStatusResponse])
async def list_all_jobs(limit: int = 20):
    details_list = await list_jobs_details(limit=limit)
    res = []
    for d in details_list:
        ret = d.get("return_value") or {}
        res.append(
            JobStatusResponse(
                id=d["id"],
                name=d["name"],
                state=d["state"],
                progress=d.get("progress"),
                return_value=ret,
                failed_reason=d.get("failed_reason"),
                master_playlist_url=ret.get("master_playlist_url"),
                quality_report=ret.get("quality_report"),
                timestamp=d.get("timestamp"),
                processed_on=d.get("processed_on"),
                finished_on=d.get("finished_on"),
                duration_seconds=d.get("duration_seconds"),
            )
        )
    return res


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    details = await get_job_status_details(job_id)
    if not details:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found")

    ret = details.get("return_value") or {}
    return JobStatusResponse(
        id=details["id"],
        name=details["name"],
        state=details["state"],
        progress=details.get("progress"),
        return_value=ret,
        failed_reason=details.get("failed_reason"),
        master_playlist_url=ret.get("master_playlist_url"),
        quality_report=ret.get("quality_report"),
        timestamp=details.get("timestamp"),
        processed_on=details.get("processed_on"),
        finished_on=details.get("finished_on"),
        duration_seconds=details.get("duration_seconds"),
    )


@router.post("/jobs/{job_id}/cancel")
async def cancel_job_endpoint(job_id: str):
    job = await get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found")
    await cancel_job(job_id)
    return {"job_id": job_id, "status": "cancelling"}


@router.delete("/jobs/{job_id}")
async def delete_job_endpoint(job_id: str):
    await delete_job(job_id)
    return {"job_id": job_id, "status": "deleted"}


@router.get("/jobs/{job_id}/playback", response_model=PlaybackResponse)
async def get_playback_urls(job_id: str):
    details = await get_job_status_details(job_id)
    if not details:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    if details.get("state") != "completed":
        raise HTTPException(status_code=400, detail=f"Job '{job_id}' not completed ({details.get('state')})")

    ret = details.get("return_value", {})
    master_key = ret.get("master_playlist_key", f"hls/{job_id}/master.m3u8")
    master_url = ret.get("master_playlist_url") or s3_storage.get_object_url(settings.S3_BUCKET_PROCESSED, master_key)

    presigned_url = s3_storage.generate_presigned_download_url(
        bucket=settings.S3_BUCKET_PROCESSED,
        s3_key=master_key,
        expires_in=86400,
    )
    return PlaybackResponse(job_id=job_id, master_playlist_url=master_url, presigned_playback_url=presigned_url)


@router.get("/jobs/{job_id}/metrics")
async def get_job_quality_metrics(job_id: str) -> Dict[str, Any]:
    details = await get_job_status_details(job_id)
    if not details:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    qreport = (details.get("return_value") or {}).get("quality_report")
    if not qreport:
        raise HTTPException(status_code=404, detail=f"Metrics not available for '{job_id}'")
    return qreport
