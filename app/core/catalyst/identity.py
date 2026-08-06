"""Procesamiento de identidad D01/D06 — clave_cotejo y ancla_origen (Caso 3)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.core.catalyst.governance import SOURCE_ID_COLUMN
from app.core.catalyst.models import ConfigRow


@dataclass(frozen=True)
class RowIdentity:
    """Identidad de fila para bóveda SCD2."""

    clave_cotejo: str
    ancla_origen: str | None


def aggressive_normalize(value: str) -> str:
    """Limpieza agresiva: sin espacios, sin acentos, todo mayúsculas."""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"\s+", "", text)
    return text.upper()


def build_clave_cotejo(
    row: dict[str, Any],
    key_columns: list[ConfigRow],
    separador_llave: str,
) -> str:
    """Concatena llaves humanas por orden_llave y genera clave_cotejo."""
    ordered = sorted(key_columns, key=lambda item: item.orden_llave)
    parts: list[str] = []
    for config in ordered:
        raw = row.get(config.propiedad)
        parts.append("" if raw is None else str(raw))

    raw_key = separador_llave.join(parts)
    return aggressive_normalize(raw_key)


def resolve_row_identity(
    row: dict[str, Any],
    key_columns: list[ConfigRow],
    separador_llave: str,
) -> RowIdentity:
    """D01/D06 con Caso 3: sin llave_humana usa id de a_1 como ancla_origen."""
    row_id = row.get(SOURCE_ID_COLUMN)
    ancla_origen = None if row_id is None else str(row_id)

    if key_columns:
        return RowIdentity(
            clave_cotejo=build_clave_cotejo(row, key_columns, separador_llave),
            ancla_origen=ancla_origen,
        )

    if ancla_origen is None:
        raise ValueError(
            "D01 Caso 3: la fila no tiene llave_humana y la tabla origen no expone "
            f"columna '{SOURCE_ID_COLUMN}' para usar como ancla_origen."
        )

    return RowIdentity(
        clave_cotejo=aggressive_normalize(f"ANCLA:{ancla_origen}"),
        ancla_origen=ancla_origen,
    )
