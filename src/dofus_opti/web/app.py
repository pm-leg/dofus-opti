"""API HTTP de l'optimiseur — usage local.

    python -m dofus_opti.web
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .service import BadRequest, OptimizerService

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="dofus-opti", docs_url=None, redoc_url=None)
service = OptimizerService()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta() -> dict:
    return service.meta()


@app.get("/api/spells")
def spells(breed: str, level: int = 200) -> list[dict]:
    try:
        return service.spells_for(breed, level)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/items")
def items(q: str, level: int = 200) -> list[dict]:
    if len(q) < 2:
        return []
    return service.items_matching(q, level)


@app.get("/api/monsters")
def monsters(q: str) -> list[str]:
    if len(q) < 2:
        return []
    return service.monsters_matching(q)


@app.post("/api/optimize")
def optimize(payload: dict) -> dict:
    try:
        return service.submit(payload)
    except BadRequest as exc:
        raise HTTPException(400, str(exc)) from exc
    except (LookupError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    try:
        return service.status(job_id)
    except BadRequest as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/publish/{job_id}")
def publish(job_id: str) -> dict:
    """Publication sur DofusDB — uniquement sur clic explicite de l'utilisateur."""
    try:
        return service.publish(job_id)
    except BadRequest as exc:
        raise HTTPException(400, str(exc)) from exc
