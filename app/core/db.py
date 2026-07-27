import logging
from functools import lru_cache
from urllib.parse import urlparse

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.core.config import DB_URL

logger = logging.getLogger(__name__)


def normalize_database_url(url: str) -> str:
    """Normaliza la URL para SQLAlchemy/psycopg2 (postgresql://)."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def get_database_target(url: str) -> str:
    """Devuelve host:puerto/base sin credenciales (seguro para logs)."""
    parsed = urlparse(normalize_database_url(url))
    host = parsed.hostname or "unknown"
    port = parsed.port or 5432
    database = parsed.path.lstrip("/") or "unknown"
    return f"{host}:{port}/{database}"


@lru_cache
def get_db_engine() -> Engine:
    if not DB_URL:
        raise RuntimeError(
            "DATABASE_URL no está configurada. "
            "Configure la variable de entorno antes de conectarse a PostgreSQL."
        )

    db_url = normalize_database_url(DB_URL)
    target = get_database_target(db_url)

    logger.info("Inicializando engine SQLAlchemy — destino=%s", target)

    return create_engine(
        db_url,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"connect_timeout": 10},
    )
