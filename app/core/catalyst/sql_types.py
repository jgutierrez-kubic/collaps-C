"""Mapeo de tipo_dato_generico RMS a tipos PostgreSQL para materialización."""

from __future__ import annotations

from app.core.catalyst.cleanup import NUMERIC_TYPES


def tipo_dato_to_pg_type(tipo_dato_generico: str) -> str:
    """Devuelve el tipo PostgreSQL para una columna materializada."""
    tipo = tipo_dato_generico.strip().lower()
    if tipo in NUMERIC_TYPES:
        return "NUMERIC"
    return "TEXT"
