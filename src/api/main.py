from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router as api_router
from src.api.tus import router as tus_router
from src.config import settings
from src.s3 import s3_storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        s3_storage.ensure_buckets()
    except Exception as exc:
        print(f"S3 init warning: {exc}")
    yield


app = FastAPI(title="Video Processing Pipeline", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "Location",
        "Upload-Offset",
        "Upload-Length",
        "Tus-Version",
        "Tus-Resumable",
        "Tus-Max-Size",
        "Tus-Extension",
        "Upload-Metadata",
        "X-Job-Id",
    ],
)

app.include_router(api_router)
app.include_router(tus_router)


@app.get("/", tags=["General"])
async def root():
    return {
        "service": "Video Processing Pipeline",
        "docs": "/docs",
        "tus": "/api/v1/tus",
        "bull_studio": "http://localhost:4000",
        "s3_console": settings.S3_CONSOLE_URL,
    }


@app.get("/health", tags=["General"])
async def health():
    return {
        "status": "healthy",
        "queue": settings.QUEUE_NAME,
        "s3": settings.S3_ENDPOINT_URL,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api.main:app", host=settings.API_HOST, port=settings.API_PORT, reload=True)
