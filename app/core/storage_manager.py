import logging
from pathlib import Path
from typing import BinaryIO

from app.core.config import GCS_BUCKET_NAME

logger = logging.getLogger(__name__)

LOCAL_DATA_DIR = Path("data")


class StorageManager:
    @staticmethod
    def upload_to_gcs(
        file_obj: BinaryIO,
        filename: str,
        project_id: str,
        subfolder: str = "docs",
    ) -> str:
        if not filename:
            raise ValueError("El archivo subido no tiene nombre.")

        blob_path = f"{project_id}/{subfolder}/{filename}"

        try:
            from google.cloud import storage

            client = storage.Client()
            bucket = client.bucket(GCS_BUCKET_NAME)
            blob = bucket.blob(blob_path)

            file_obj.seek(0)
            blob.upload_from_file(file_obj)

            gcs_uri = f"gs://{GCS_BUCKET_NAME}/{blob_path}"
            logger.info("Archivo subido a GCS: %s", gcs_uri)
            return gcs_uri
        except Exception as exc:
            logger.warning(
                "GCS no disponible (credenciales o conectividad) — fallback local: %s",
                exc,
            )
            return StorageManager._save_local_fallback(file_obj, filename)

    @staticmethod
    def _save_local_fallback(file_obj: BinaryIO, filename: str) -> str:
        LOCAL_DATA_DIR.mkdir(exist_ok=True)
        local_path = LOCAL_DATA_DIR / filename

        file_obj.seek(0)
        local_path.write_bytes(file_obj.read())

        local_uri = f"local://data/{filename}"
        logger.info("Archivo guardado localmente: %s", local_path)
        return local_uri
