from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.api import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    yield


app = FastAPI(
    title="School Bus Tracking API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(api_router)

