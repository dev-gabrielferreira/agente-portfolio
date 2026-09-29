import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.config import settings

logging.basicConfig(
    level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger(__name__)

# build do Vite (frontend/dist); em dev o Vite serve o frontend e faz proxy de /api para cá
DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    log.info("starting %s env=%s", settings.app_name, settings.app_env)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
api = APIRouter(prefix="/api")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@api.get("/hello")
def hello() -> dict[str, str]:
    return {"message": f"Olá do {settings.app_name}"}


app.include_router(api)


if DIST.is_dir():  # a SPA fica por último: /api e /health têm prioridade

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        """Arquivos do build ou o index.html (rotas do React). Só GET: outros métodos recebem 405
        com o cabeçalho Allow, e /api/* inexistente continua 404 em vez de virar página."""
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        target = (DIST / path).resolve()
        if path and target.is_file() and target.is_relative_to(DIST):
            return FileResponse(target)
        return FileResponse(DIST / "index.html")
