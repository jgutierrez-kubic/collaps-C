from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConfigRow:
    """Fila de mapeo leída desde la tabla de configuración de ingesta (RMS Genérico v1.4)."""

    columna_origen: str
    tipo_dato_generico: str
    es_llave: bool
    guardar: bool

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> ConfigRow:
        def pick(*keys: str, default: Any = "") -> Any:
            for key in keys:
                if key in row and row[key] is not None:
                    return row[key]
            return default

        return cls(
            columna_origen=str(pick("columna_origen", "columnaOrigen")).strip(),
            tipo_dato_generico=str(
                pick("tipo_dato_generico", "tipoDatoGenerico", default="texto")
            ).strip(),
            es_llave=bool(pick("es_llave", "esLlave", default=False)),
            guardar=bool(pick("guardar", default=True)),
        )


@dataclass
class JobSummary:
    """Resumen acumulado del job Catalyst."""

    filas_procesadas: int = 0
    filas_omitidas: int = 0
    registros_insertados: int = 0
    registros_actualizados: int = 0
    registros_sin_cambio: int = 0
    errores: list[str] | None = None

    def __post_init__(self) -> None:
        if self.errores is None:
            self.errores = []

    def to_callback_dict(self) -> dict[str, Any]:
        return {
            "filasProcesadas": self.filas_procesadas,
            "filasOmitidas": self.filas_omitidas,
            "registrosInsertados": self.registros_insertados,
            "registrosActualizados": self.registros_actualizados,
            "registrosSinCambio": self.registros_sin_cambio,
            "errores": self.errores or [],
        }
