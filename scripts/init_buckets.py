import time
import logging
from src.s3 import s3_storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("init-s3")


def main():
    logger.info("Initializing S3 buckets...")
    for attempt in range(1, 16):
        try:
            s3_storage.ensure_buckets()
            logger.info("Buckets verified")
            return
        except Exception as exc:
            logger.warning(f"Attempt {attempt}/15: {exc}")
            time.sleep(2)
    logger.error("Failed to initialize buckets")


if __name__ == "__main__":
    main()
