"""Gestión de identidad RMS — entidad_interna_id determinístico por es_llave."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.core.catalyst.governance import SOURCE_ID_COLUMN
from app.core.catalyst.models import ConfigRow

CATALYST_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


@dataclass(frozen=True)
class RowIdentity:
    """Identidad de fila para bóveda SCD2 y registro en a_2_identidad."""

    entidad_interna_id: str
    llave_humana_completa: str


def build_llave_humana_completa(
    row: dict[str, Any],
    key_columns: list[ConfigRow],
    separador_llave: str,
) -> str:
    """Concatena valores de columnas es_llave en orden alfabético de columna."""
    ordered = sorted(key_columns, key=lambda item: item.columna_origen)
    parts: list[str] = []
    for config in ordered:
        raw = row.get(config.columna_origen)
        parts.append("" if raw is None else str(raw))
    return separador_llave.join(parts)


def build_entidad_interna_id(key_material: str) -> str:
    """UUID v5 determinístico: misma llave → mismo UUID."""
    return str(uuid.uuid5(CATALYST_NAMESPACE, key_material))


def resolve_row_identity(
    row: dict[str, Any],
    key_columns: list[ConfigRow],
    separador_llave: str,
) -> RowIdentity:
    """Genera entidad_interna_id y llave_humana_completa desde columnas es_llave."""
    if key_columns:
        llave_humana_completa = build_llave_humana_completa(row, key_columns, separador_llave)
    else:
        row_id = row.get(SOURCE_ID_COLUMN)
        if row_id is None:
            raise ValueError(
                "La fila no tiene columnas es_llave y la tabla origen no expone "
                f"columna '{SOURCE_ID_COLUMN}' para generar entidad_interna_id."
            )
        llave_humana_completa = f"ANCLA:{row_id}"

    return RowIdentity(
        entidad_interna_id=build_entidad_interna_id(llave_humana_completa),
        llave_humana_completa=llave_humana_completa,
    )
