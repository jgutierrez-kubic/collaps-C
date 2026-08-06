from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import JSONResponse

from app.core.catalyst.catalyst_engine import CatalystEngine
from app.models.catalyst_payload import CatalystJobPayload

router = APIRouter(prefix="/api/v1/catalyst", tags=["catalyst"])


@router.post(
    "/job",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Encolar job Catalyst (refiner COLLAPS v1.3)",
)
async def create_catalyst_job(
    payload: CatalystJobPayload,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Refina tabla origen (a_1) hacia bóveda KV (a_3_boveda_kv) según a_2_config."""
    job_id = str(uuid4())
    engine = CatalystEngine(payload)
    background_tasks.add_task(engine.run, job_id)

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "jobId": job_id,
            "schemaName": payload.schema_name,
            "sourceTable": payload.source_table,
            "message": "Catalyst job queued successfully",
        },
    )
