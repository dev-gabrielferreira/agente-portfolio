import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings

logging.basicConfig(
    level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    log.info("starting %s env=%s", settings.app_name, settings.app_env)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
