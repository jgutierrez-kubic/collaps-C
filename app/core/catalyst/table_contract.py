"""Contrato de tablas Catalyst — sin nombres fijos en lógica de negocio."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from app.models.payload import sanitize_table_identifier

_INDEX_SUFFIX_RE = re.compile(r"[^a-zA-Z0-9_]+")


def table_index_suffix(table_name: str) -> str:
    return _INDEX_SUFFIX_RE.sub("_", table_name).strip("_")


@dataclass(frozen=True)
class CatalystTableContract:
    """Nombres de tablas resueltos desde el job o variables de entorno."""

    config_table: str
    boveda_table: str
    identidad_table: str

    @classmethod
    def from_job_fields(
        cls,
        *,
        config_table: str | None,
        boveda_table: str | None,
        identidad_table: str | None,
    ) -> CatalystTableContract:
        resolved_config = _resolve_table_name(config_table, "CATALYST_CONFIG_TABLE")
        resolved_boveda = _resolve_table_name(boveda_table, "CATALYST_BOVEDA_TABLE")
        resolved_identidad = _resolve_table_name(identidad_table, "CATALYST_IDENTIDAD_TABLE")
        return cls(
            config_table=resolved_config,
            boveda_table=resolved_boveda,
            identidad_table=resolved_identidad,
        )

    def index_suffix(self, table_name: str) -> str:
        return table_index_suffix(table_name)


def _resolve_table_name(job_value: str | None, env_var: str) -> str:
    candidate = (job_value or os.getenv(env_var) or "").strip()
    if not candidate:
        raise ValueError(
            f"Nombre de tabla requerido: defina el campo en el job o la variable {env_var}."
        )
    return sanitize_table_identifier(candidate)
