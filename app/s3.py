import mimetypes
from pathlib import Path

import boto3
from botocore.client import Config

from app.config import cfg

mimetypes.add_type("application/x-mpegURL", ".m3u8")
mimetypes.add_type("video/MP2T", ".ts")

client = boto3.client(
    "s3",
    endpoint_url=cfg.s3_url,
    aws_access_key_id=cfg.s3_key,
    aws_secret_access_key=cfg.s3_secret,
    region_name=cfg.s3_region,
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)


def ensure_buckets() -> None:
    have = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    for b in {cfg.raw_bucket, cfg.hls_bucket} - have:
        client.create_bucket(Bucket=b)


def put(path: Path, bucket: str, key: str, ctype: str | None = None) -> None:
    ctype = ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": ctype})


def put_dir(root: Path, bucket: str, prefix: str) -> None:
    for f in root.rglob("*"):
        if f.is_file():
            put(f, bucket, f"{prefix}/{f.relative_to(root).as_posix()}")


def get(bucket: str, key: str, path: Path) -> None:
    client.download_file(bucket, key, str(path))


def url(bucket: str, key: str) -> str:
    return f"{cfg.s3_url.rstrip('/')}/{bucket}/{key}"
