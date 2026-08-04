from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import JSONResponse

from app.core.worktable_engine import WorktableEngine
from app.models.worktable_payload import WorktableCreatePayload

router = APIRouter(prefix="/api/v1/worktables", tags=["worktables"])


@router.post(
    "/create",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Encolar creación de worktable materializada",
)
async def create_worktable(
    payload: WorktableCreatePayload,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Crea una worktable agrupada a partir de una tabla origen.

    Flujo previsto:
    1. Validate payload (source_table, target_table, group_by, order_by).
    2. Enqueue WorktableEngine.run() in background.
    3. Engine reads/groups in Postgres, assigns incremental run_id,
       and persists to target_table.
    """
    job_id = str(uuid4())
    engine = WorktableEngine(payload)
    background_tasks.add_task(engine.run, job_id)

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "jobId": job_id,
            "targetTable": payload.target_table,
            "message": "Worktable job queued successfully",
        },
    )
