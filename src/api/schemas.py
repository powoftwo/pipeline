from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class JobCreateRequest(BaseModel):
    s3_key: str
    s3_bucket: Optional[str] = None
    segment_duration: Optional[int] = Field(4, ge=1, le=10)
    compute_quality_metrics: Optional[bool] = True
    compute_vmaf: Optional[bool] = True
    compute_xpsnr: Optional[bool] = True
    hw_accel: Optional[bool] = False


class PresignedUploadRequest(BaseModel):
    file_name: str
    content_type: Optional[str] = "video/mp4"


class PresignedUploadResponse(BaseModel):
    upload_url: str
    s3_bucket: str
    s3_key: str


class JobStatusResponse(BaseModel):
    id: str
    name: str
    state: str
    progress: Optional[Any] = None
    return_value: Optional[Dict[str, Any]] = None
    failed_reason: Optional[str] = None
    master_playlist_url: Optional[str] = None
    quality_report: Optional[Dict[str, Any]] = None
    timestamp: Optional[int] = None
    processed_on: Optional[int] = None
    finished_on: Optional[int] = None
    duration_seconds: Optional[float] = None


class PlaybackResponse(BaseModel):
    job_id: str
    master_playlist_url: str
    presigned_playback_url: str
