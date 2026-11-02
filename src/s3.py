import mimetypes
import os
from pathlib import Path
from typing import Optional

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from src.config import settings

mimetypes.add_type("application/x-mpegURL", ".m3u8")
mimetypes.add_type("video/MP2T", ".ts")
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("application/json", ".json")


class S3Storage:
    def __init__(
        self,
        endpoint_url: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        region: Optional[str] = None,
    ):
        self.endpoint_url = endpoint_url or settings.S3_ENDPOINT_URL
        self.access_key = access_key or settings.S3_ACCESS_KEY
        self.secret_key = secret_key or settings.S3_SECRET_KEY
        self.region = region or settings.S3_REGION

        self.client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def ensure_buckets(self, buckets: Optional[list[str]] = None) -> None:
        target = buckets or [settings.S3_BUCKET_RAW, settings.S3_BUCKET_PROCESSED]
        for b in target:
            try:
                self.client.head_bucket(Bucket=b)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in ("404", "NoSuchBucket"):
                    self.client.create_bucket(Bucket=b)
                else:
                    try:
                        self.client.create_bucket(Bucket=b)
                    except Exception:
                        pass

    def upload_file(self, local_path: str | Path, bucket: str, s3_key: str, content_type: Optional[str] = None) -> str:
        local_path = Path(local_path)
        if not content_type:
            content_type, _ = mimetypes.guess_type(str(local_path))
            content_type = content_type or "application/octet-stream"

        self.client.upload_file(str(local_path), bucket, s3_key, ExtraArgs={"ContentType": content_type})
        return s3_key

    def upload_directory(self, dir_path: str | Path, bucket: str, s3_prefix: str) -> list[str]:
        dir_path = Path(dir_path)
        s3_prefix = s3_prefix.strip("/")
        keys: list[str] = []

        for root, _, files in os.walk(dir_path):
            for file_name in files:
                file_path = Path(root) / file_name
                s3_key = f"{s3_prefix}/{file_path.relative_to(dir_path).as_posix()}"
                content_type, _ = mimetypes.guess_type(str(file_path))
                if file_name.endswith(".m3u8"):
                    content_type = "application/x-mpegURL"
                elif file_name.endswith(".ts"):
                    content_type = "video/MP2T"
                elif not content_type:
                    content_type = "application/octet-stream"

                self.client.upload_file(str(file_path), bucket, s3_key, ExtraArgs={"ContentType": content_type})
                keys.append(s3_key)
        return keys

    def download_file(self, bucket: str, s3_key: str, local_path: str | Path) -> Path:
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(bucket, s3_key, str(local_path))
        return local_path

    def generate_presigned_upload_url(self, bucket: str, s3_key: str, expires_in: int = 3600) -> str:
        return self.client.generate_presigned_url("put_object", Params={"Bucket": bucket, "Key": s3_key}, ExpiresIn=expires_in)

    def generate_presigned_download_url(self, bucket: str, s3_key: str, expires_in: int = 3600) -> str:
        return self.client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": s3_key}, ExpiresIn=expires_in)

    def get_object_url(self, bucket: str, s3_key: str) -> str:
        return f"{self.endpoint_url.rstrip('/')}/{bucket}/{s3_key}"


s3_storage = S3Storage()
