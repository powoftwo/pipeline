from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    REDIS_URL: str = "redis://localhost:6379"
    QUEUE_NAME: str = "video-processing"

    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_CONSOLE_URL: str = "http://localhost:9001"
    S3_ACCESS_KEY: str = "admin"
    S3_SECRET_KEY: str = "adminpassword"
    S3_REGION: str = "us-east-1"
    S3_BUCKET_RAW: str = "videos-raw"
    S3_BUCKET_PROCESSED: str = "videos-processed"

    TEMP_DIR: str = "/tmp/video-processing"
    HLS_SEGMENT_TIME: int = 4
    COMPUTE_QUALITY_METRICS: bool = True
    COMPUTE_VMAF: bool = True
    COMPUTE_XPSNR: bool = True
    VMAF_MODEL: str = "vmaf_v0.6.1.json"
    VMAF_SUBSAMPLE: int = 1

    TUS_UPLOAD_DIR: str = "/tmp/video-processing/tus_uploads"
    TUS_MAX_SIZE: int = 10 * 1024 * 1024 * 1024

    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    @property
    def temp_path(self) -> Path:
        p = Path(self.TEMP_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def tus_path(self) -> Path:
        p = Path(self.TUS_UPLOAD_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
