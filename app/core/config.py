import os

DB_URL = os.getenv("DATABASE_URL")
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "bim-saas-storage-collaps-prod")
