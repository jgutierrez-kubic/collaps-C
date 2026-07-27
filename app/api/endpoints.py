from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, Form, UploadFile, status
from fastapi.responses import JSONResponse

from app.core.analysis_engine import AnalysisEngine
from app.core.storage_manager import StorageManager
from app.models.payload import AnalysisPayload

router = APIRouter(prefix="/api/v1/condenser", tags=["condenser"])


@router.post(
    "/job",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Encolar un análisis de cruce COLLAPS",
)
async def create_analysis_job(
    payload: AnalysisPayload,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    job_id = str(uuid4())
    engine = AnalysisEngine(payload)
    background_tasks.add_task(engine.run, job_id)

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "job_id": job_id,
            "analysis_id": payload.analysis_id,
            "message": "Análisis encolado exitosamente",
        },
    )


@router.post(
    "/upload",
    summary="Subir archivo auxiliar a GCS (o fallback local)",
)
async def upload_auxiliary_file(
    file: UploadFile = File(...),
    project_id: str = Form(...),
    subfolder: str = Form("docs"),
) -> dict[str, str]:
    result_path = StorageManager.upload_to_gcs(
        file_obj=file.file,
        filename=file.filename or "upload.bin",
        project_id=project_id,
        subfolder=subfolder,
    )

    return {"status": "success", "gcs_path": result_path}
