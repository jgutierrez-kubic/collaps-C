import logging

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.endpoints import router as condenser_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="Condenser CORE",
    description="Motor asíncrono de procesamiento de datos — Módulo C (Collpaps BIM-OS)",
    version="0.1.0",
)

app.include_router(condenser_router)
app.mount("/app", StaticFiles(directory="static", html=True), name="static")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/app")
