from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from src.config import settings
from src.tus.manager import encode_tus_metadata, parse_tus_metadata, tus_manager

router = APIRouter(prefix="/api/v1/tus", tags=["TUS"])

TUS_VER = "1.0.0"
TUS_EXT = "creation,creation-with-upload,termination"


def check_version(val: Optional[str]) -> None:
    if val is not None and val != TUS_VER:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=f"Unsupported TUS version: {val}",
            headers={"Tus-Version": TUS_VER},
        )


def tus_headers() -> dict[str, str]:
    return {
        "Tus-Resumable": TUS_VER,
        "Tus-Version": TUS_VER,
        "Tus-Extension": TUS_EXT,
        "Tus-Max-Size": str(settings.TUS_MAX_SIZE),
        "Access-Control-Expose-Headers": (
            "Location, Upload-Offset, Upload-Length, Tus-Version, "
            "Tus-Resumable, Tus-Max-Size, Tus-Extension, Upload-Metadata, X-Job-Id"
        ),
    }


@router.options("")
@router.options("/{upload_id}")
async def options():
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=tus_headers())


@router.post("")
async def create_upload(
    request: Request,
    upload_length: Optional[str] = Header(None, alias="Upload-Length"),
    upload_metadata: Optional[str] = Header(None, alias="Upload-Metadata"),
    tus_resumable: Optional[str] = Header(None, alias="Tus-Resumable"),
    content_type: Optional[str] = Header(None, alias="Content-Type"),
    upload_offset: Optional[str] = Header(None, alias="Upload-Offset"),
):
    check_version(tus_resumable)

    if not upload_length or not upload_length.isdigit():
        raise HTTPException(400, "Invalid Upload-Length", headers={"Tus-Resumable": TUS_VER})

    length = int(upload_length)
    if length > settings.TUS_MAX_SIZE:
        raise HTTPException(413, "Size exceeds limit", headers={"Tus-Resumable": TUS_VER, "Tus-Max-Size": str(settings.TUS_MAX_SIZE)})

    upload = tus_manager.create_upload(upload_length=length, metadata=parse_tus_metadata(upload_metadata))
    loc = f"{str(request.base_url).rstrip('/')}/api/v1/tus/{upload.upload_id}"

    headers = tus_headers()
    headers["Location"] = loc
    headers["Upload-Offset"] = "0"

    if content_type == "application/offset+octet-stream" and upload_offset == "0":
        cur = 0
        async for chunk in request.stream():
            if chunk:
                cur = tus_manager.append_chunk(upload.upload_id, cur, chunk)
        tus_manager.update_offset(upload, cur)
        headers["Upload-Offset"] = str(cur)

        if cur == length:
            upload = await tus_manager.complete_upload(upload)
            if upload.job_id:
                headers["X-Job-Id"] = upload.job_id

    return Response(status_code=status.HTTP_201_CREATED, headers=headers)


@router.head("/{upload_id}")
async def get_upload_head(upload_id: str, tus_resumable: Optional[str] = Header(None, alias="Tus-Resumable")):
    check_version(tus_resumable)

    upload = tus_manager.get_upload(upload_id)
    if not upload:
        raise HTTPException(404, f"Upload '{upload_id}' not found", headers={"Tus-Resumable": TUS_VER})

    headers = tus_headers()
    headers["Upload-Offset"] = str(upload.offset)
    headers["Upload-Length"] = str(upload.upload_length)
    headers["Cache-Control"] = "no-store"
    if upload.metadata:
        headers["Upload-Metadata"] = encode_tus_metadata(upload.metadata)
    if upload.job_id:
        headers["X-Job-Id"] = upload.job_id

    return Response(status_code=status.HTTP_200_OK, headers=headers)


@router.patch("/{upload_id}")
async def patch_upload(
    upload_id: str,
    request: Request,
    upload_offset: Optional[str] = Header(None, alias="Upload-Offset"),
    content_type: Optional[str] = Header(None, alias="Content-Type"),
    tus_resumable: Optional[str] = Header(None, alias="Tus-Resumable"),
):
    check_version(tus_resumable)

    upload = tus_manager.get_upload(upload_id)
    if not upload:
        raise HTTPException(404, f"Upload '{upload_id}' not found", headers={"Tus-Resumable": TUS_VER})

    if not content_type or not content_type.startswith("application/offset+octet-stream"):
        raise HTTPException(415, "Content-Type must be application/offset+octet-stream", headers={"Tus-Resumable": TUS_VER})

    if upload_offset is None or not upload_offset.isdigit():
        raise HTTPException(400, "Invalid Upload-Offset", headers={"Tus-Resumable": TUS_VER})

    if int(upload_offset) != upload.offset:
        raise HTTPException(409, f"Offset mismatch: {upload.offset} != {upload_offset}", headers={"Tus-Resumable": TUS_VER, "Upload-Offset": str(upload.offset)})

    cur = upload.offset
    async for chunk in request.stream():
        if chunk:
            cur = tus_manager.append_chunk(upload_id, cur, chunk)

    if cur > upload.upload_length:
        raise HTTPException(400, "Bytes exceeded declared length", headers={"Tus-Resumable": TUS_VER})

    tus_manager.update_offset(upload, cur)

    headers = tus_headers()
    headers["Upload-Offset"] = str(cur)

    if cur == upload.upload_length and not upload.is_completed:
        upload = await tus_manager.complete_upload(upload)
        if upload.job_id:
            headers["X-Job-Id"] = upload.job_id
            headers["Location"] = f"/api/v1/jobs/{upload.job_id}"

    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=headers)


@router.delete("/{upload_id}")
async def delete_upload(upload_id: str, tus_resumable: Optional[str] = Header(None, alias="Tus-Resumable")):
    check_version(tus_resumable)
    upload = tus_manager.get_upload(upload_id)
    if not upload:
        raise HTTPException(404, f"Upload '{upload_id}' not found", headers={"Tus-Resumable": TUS_VER})
    tus_manager.delete_upload(upload_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Tus-Resumable": TUS_VER})


@router.get("/{upload_id}")
async def get_upload_status(upload_id: str):
    upload = tus_manager.get_upload(upload_id)
    if not upload:
        raise HTTPException(404, f"Upload '{upload_id}' not found")

    pct = round((upload.offset / upload.upload_length) * 100.0, 1) if upload.upload_length > 0 else 0.0
    return {
        "upload_id": upload.upload_id,
        "filename": upload.filename,
        "upload_length": upload.upload_length,
        "offset": upload.offset,
        "percent": pct,
        "is_completed": upload.is_completed,
        "s3_bucket": upload.s3_bucket,
        "s3_key": upload.s3_key,
        "job_id": upload.job_id,
        "metadata": upload.metadata,
        "job_url": f"/api/v1/jobs/{upload.job_id}" if upload.job_id else None,
    }
