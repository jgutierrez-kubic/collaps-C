"""Contrato de tablas Catalyst — resolución centralizada sin hardcode disperso."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from app.models.payload import sanitize_table_identifier

_INDEX_SUFFIX_RE = re.compile(r"[^a-zA-Z0-9_]+")

# Defaults RMS v1.4 — única fuente de verdad; sobreescribibles por job o env.
RMS_V14_ENV_DEFAULTS: dict[str, str] = {
    "CATALYST_CONFIG_TABLE": "a_2_config_ingesta_a",
    "CATALYST_BOVEDA_TABLE": "a_3_boveda_kv",
    "CATALYST_IDENTIDAD_TABLE": "a_2_identidad",
}


def qualified_table(schema_name: str, table_name: str) -> str:
    """Califica tabla con schemaName del job: \"{schema}\".\"{table}\"."""
    return f'"{schema_name}"."{table_name}"'


def table_index_suffix(table_name: str) -> str:
    return _INDEX_SUFFIX_RE.sub("_", table_name).strip("_")


@dataclass(frozen=True)
class CatalystTableContract:
    """Nombres de tablas resueltos: job → env → defaults RMS v1.4."""

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
        return cls(
            config_table=_resolve_table_name(
                config_table, "CATALYST_CONFIG_TABLE"
            ),
            boveda_table=_resolve_table_name(
                boveda_table, "CATALYST_BOVEDA_TABLE"
            ),
            identidad_table=_resolve_table_name(
                identidad_table, "CATALYST_IDENTIDAD_TABLE"
            ),
        )

    def index_suffix(self, table_name: str) -> str:
        return table_index_suffix(table_name)


def _resolve_table_name(job_value: str | None, env_var: str) -> str:
    candidate = (
        job_value
        or os.getenv(env_var)
        or RMS_V14_ENV_DEFAULTS.get(env_var)
        or ""
    ).strip()
    if not candidate:
        raise ValueError(
            f"Nombre de tabla requerido: defina el campo en el job, "
            f"la variable {env_var}, o el default RMS v1.4."
        )
    return sanitize_table_identifier(candidate)
