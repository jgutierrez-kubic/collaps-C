"""Controles de gobernanza Catalyst — filtro req_aceptado y detección de columnas."""

from __future__ import annotations

from typing import Any

REQ_ACEPTADO_COLUMN = "req_aceptado"
SOURCE_ID_COLUMN = "id"


def source_table_has_column(column_names: set[str], column: str) -> bool:
    return column.lower() in {name.lower() for name in column_names}


def is_req_aceptado(value: Any) -> bool:
    """Evalúa req_aceptado de forma tolerante a tipos heterogéneos."""
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "t", "1", "yes", "y", "si", "sí"}
    return bool(value)


def row_passes_acceptance_filter(row: dict[str, Any], *, has_req_aceptado: bool) -> bool:
    """Si la tabla tiene req_aceptado, solo acepta filas con valor truthy."""
    if not has_req_aceptado:
        return True
    return is_req_aceptado(row.get(REQ_ACEPTADO_COLUMN))
