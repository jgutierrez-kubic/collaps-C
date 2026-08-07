import logging
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.catalyst.catalyst_engine import CatalystEngine
from app.core.catalyst.materialize_engine import CatalystMaterializeEngine
from app.core.catalyst.origen_dato import ORIGEN_EDICION_MANUAL
from app.core.catalyst.sync_back_engine import CatalystSyncBackEngine
from app.models.catalyst_payload import (
    CatalystJobPayload,
    CatalystMaterializePayload,
    CatalystSyncBackPayload,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/catalyst", tags=["catalyst"])


def _error_response(
    *,
    status_code: int,
    error: str,
    error_type: str,
    job_id: str | None = None,
) -> JSONResponse:
    content: dict[str, object] = {
        "status": "error",
        "error": error,
        "errorType": error_type,
    }
    if job_id:
        content["jobId"] = job_id
    return JSONResponse(status_code=status_code, content=content)


@router.post(
    "/job",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Encolar job Catalyst (refiner RMS Genérico v1.4)",
)
async def create_catalyst_job(
    payload: CatalystJobPayload,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Refina tabla origen hacia bóveda KV según configuración RMS v1.4."""
    job_id = str(uuid4())

    try:
        engine = CatalystEngine(payload)
        background_tasks.add_task(engine.run, job_id)
    except (ValueError, RuntimeError) as exc:
        logger.warning("Catalyst job rechazado — job_id=%s: %s", job_id, exc)
        return _error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            error=str(exc),
            error_type="catalyst_job_rejected",
            job_id=job_id,
        )
    except ValidationError as exc:
        logger.warning("Catalyst payload inválido — job_id=%s: %s", job_id, exc)
        return _error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error=str(exc),
            error_type="validation_error",
            job_id=job_id,
        )
    except Exception as exc:
        logger.exception("Error inesperado al encolar Catalyst job_id=%s", job_id)
        return _error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error=str(exc),
            error_type="internal_error",
            job_id=job_id,
        )

    tables = payload.resolve_tables()
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "jobId": job_id,
            "schemaName": payload.schema_name,
            "sourceTable": payload.source_table,
            "configTable": tables.config_table,
            "bovedaTable": tables.boveda_table,
            "identidadTable": tables.identidad_table,
            "message": "Catalyst job queued successfully",
        },
    )


@router.post(
    "/materialize",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Encolar materialización Capa 4 (bóveda vigente → a_4_*)",
)
async def create_catalyst_materialize_job(
    payload: CatalystMaterializePayload,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Materializa registros VIGENTES de bóveda en tabla ancha a_4_*."""
    job_id = str(uuid4())

    try:
        engine = CatalystMaterializeEngine(payload)
        background_tasks.add_task(engine.run, job_id)
    except (ValueError, RuntimeError) as exc:
        logger.warning("Catalyst materialize rechazado — job_id=%s: %s", job_id, exc)
        return _error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            error=str(exc),
            error_type="catalyst_materialize_rejected",
            job_id=job_id,
        )
    except ValidationError as exc:
        logger.warning("Catalyst materialize payload inválido — job_id=%s: %s", job_id, exc)
        return _error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error=str(exc),
            error_type="validation_error",
            job_id=job_id,
        )
    except Exception as exc:
        logger.exception("Error inesperado al encolar materialize job_id=%s", job_id)
        return _error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error=str(exc),
            error_type="internal_error",
            job_id=job_id,
        )

    tables = payload.resolve_tables()
    target_table = payload.target_table
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "jobId": job_id,
            "schemaName": payload.schema_name,
            "sourceTable": payload.source_table,
            "targetTable": target_table,
            "configTable": tables.config_table,
            "bovedaTable": tables.boveda_table,
            "message": "Catalyst materialize job queued successfully",
        },
    )


@router.post(
    "/sync-back",
    status_code=status.HTTP_200_OK,
    summary="Sync-back: edición manual de propiedad en bóveda SCD2",
)
async def catalyst_sync_back(payload: CatalystSyncBackPayload) -> JSONResponse:
    """Aplica SCD2 en bóveda con origen edicion_manual desde edición de usuario."""
    try:
        engine = CatalystSyncBackEngine(payload)
        summary = engine.run()
    except (ValueError, RuntimeError) as exc:
        logger.warning("Catalyst sync-back rechazado: %s", exc)
        return _error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            error=str(exc),
            error_type="catalyst_sync_back_rejected",
        )
    except ValidationError as exc:
        logger.warning("Catalyst sync-back payload inválido: %s", exc)
        return _error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error=str(exc),
            error_type="validation_error",
        )
    except Exception as exc:
        logger.exception("Error inesperado en sync-back")
        return _error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error=str(exc),
            error_type="internal_error",
        )

    tables = payload.resolve_tables()
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "success",
            "schemaName": payload.schema_name,
            "sourceTable": payload.source_table,
            "entidadInternaId": payload.entidad_interna_id,
            "propiedad": payload.propiedad,
            "origenDato": ORIGEN_EDICION_MANUAL,
            "usuarioEmail": payload.usuario_email,
            "configTable": tables.config_table,
            "bovedaTable": tables.boveda_table,
            "identidadTable": tables.identidad_table,
            "summary": summary.to_response_dict(),
        },
    )
