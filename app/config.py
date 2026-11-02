from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = "redis://localhost:6379"
    queue: str = "video"

    s3_url: str = "http://localhost:9000"
    s3_key: str = "admin"
    s3_secret: str = "adminpassword"
    s3_region: str = "us-east-1"
    raw_bucket: str = "raw"
    hls_bucket: str = "hls"

    tmp_dir: Path = Path("/tmp/pipeline")
    tus_max: int = 10 * 1024**3


cfg = Settings()
cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
