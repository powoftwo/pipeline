import asyncio
import base64
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from src.config import settings
from src.queue import enqueue_video_job
from src.s3 import s3_storage

logger = logging.getLogger("tus")


@dataclass
class TusUpload:
    upload_id: str
    upload_length: int
    offset: int = 0
    metadata: Dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    is_completed: bool = False
    s3_key: Optional[str] = None
    s3_bucket: Optional[str] = None
    job_id: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)

    @property
    def filename(self) -> str:
        name = self.metadata.get("filename") or self.metadata.get("name") or f"video_{self.upload_id}.mp4"
        return Path(name).name

    @property
    def content_type(self) -> str:
        return self.metadata.get("filetype") or self.metadata.get("type") or "video/mp4"


def parse_tus_metadata(header: Optional[str]) -> Dict[str, str]:
    if not header:
        return {}
    res = {}
    for item in header.split(","):
        p = item.strip().split()
        if not p:
            continue
        k = p[0]
        if len(p) > 1:
            try:
                res[k] = base64.b64decode(p[1]).decode("utf-8", errors="replace")
            except Exception:
                res[k] = p[1]
        else:
            res[k] = ""
    return res


def encode_tus_metadata(meta: Dict[str, str]) -> str:
    return ",".join(f"{k} {base64.b64encode(str(v).encode()).decode()}" for k, v in meta.items())


class TusManager:
    def __init__(self, upload_dir: Optional[Path] = None):
        self.upload_dir = upload_dir or settings.tus_path
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def _part(self, uid: str) -> Path:
        return self.upload_dir / f"{uid}.part"

    def _info(self, uid: str) -> Path:
        return self.upload_dir / f"{uid}.info"

    def _save(self, upload: TusUpload) -> None:
        with open(self._info(upload.upload_id), "w", encoding="utf-8") as f:
            json.dump(asdict(upload), f)

    def create_upload(self, upload_length: int, metadata: Optional[Dict[str, str]] = None, options: Optional[Dict[str, Any]] = None) -> TusUpload:
        uid = str(uuid.uuid4())
        upload = TusUpload(upload_id=uid, upload_length=upload_length, metadata=metadata or {}, options=options or {})
        self._part(uid).touch(exist_ok=True)
        self._save(upload)
        logger.info(f"TUS created {uid}: {upload_length} bytes, file={upload.filename}")
        return upload

    def get_upload(self, upload_id: str) -> Optional[TusUpload]:
        path = self._info(upload_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                up = TusUpload(**json.load(f))
            part = self._part(upload_id)
            if part.exists() and not up.is_completed:
                up.offset = part.stat().st_size
            return up
        except Exception as exc:
            logger.error(f"TUS read error {upload_id}: {exc}")
            return None

    def append_chunk(self, upload_id: str, offset: int, chunk: bytes) -> int:
        part = self._part(upload_id)
        if not part.exists():
            raise FileNotFoundError(f"Part missing: {upload_id}")
        if part.stat().st_size != offset:
            raise ValueError(f"Offset mismatch: {part.stat().st_size} != {offset}")
        with open(part, "ab") as f:
            f.write(chunk)
        return part.stat().st_size

    def update_offset(self, upload: TusUpload, new_offset: int) -> None:
        upload.offset = new_offset
        self._save(upload)

    async def complete_upload(self, upload: TusUpload) -> TusUpload:
        uid = upload.upload_id
        part = self._part(uid)
        bucket = settings.S3_BUCKET_RAW
        key = f"uploads/tus_{uid}/{upload.filename}"

        logger.info(f"TUS upload complete {uid}. S3: {bucket}/{key}")
        await asyncio.to_thread(s3_storage.upload_file, part, bucket, key, upload.content_type)

        upload.s3_bucket = bucket
        upload.s3_key = key
        upload.is_completed = True

        auto_process = upload.metadata.get("auto_process", "true").lower() in ("true", "1", "yes")
        if auto_process:
            opts = {
                "segment_duration": int(upload.metadata.get("segment_duration", settings.HLS_SEGMENT_TIME)),
                "compute_quality_metrics": upload.metadata.get("compute_quality_metrics", "true").lower() in ("true", "1", "yes"),
                "compute_vmaf": upload.metadata.get("compute_vmaf", "true").lower() in ("true", "1", "yes"),
                "compute_xpsnr": upload.metadata.get("compute_xpsnr", "true").lower() in ("true", "1", "yes"),
                "hw_accel": upload.metadata.get("hw_accel", "false").lower() in ("true", "1", "yes"),
            }
            try:
                job = await enqueue_video_job(s3_key=key, bucket=bucket, options=opts, job_id=uid)
                upload.job_id = str(job.id)
                logger.info(f"Enqueued job {upload.job_id}")
            except Exception as exc:
                logger.error(f"BullMQ error: {exc}")

        self._save(upload)
        part.unlink(missing_ok=True)
        return upload

    def delete_upload(self, upload_id: str) -> bool:
        self._part(upload_id).unlink(missing_ok=True)
        self._info(upload_id).unlink(missing_ok=True)
        return True


tus_manager = TusManager()
