from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api import webhook
from app.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    yield


app = FastAPI(title="PR Code Review Agent", version="0.1.0", lifespan=lifespan)

app.include_router(webhook.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
