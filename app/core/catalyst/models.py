from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConfigRow:
    """Fila de mapeo leída desde a_2_config."""

    propiedad: str
    rol: str
    orden_llave: int
    formato_entrada: str
    regla_limpieza: str
    unidad_esperada: str
    parametro: str
    guardar: bool

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> ConfigRow:
        def pick(*keys: str, default: Any = "") -> Any:
            for key in keys:
                if key in row and row[key] is not None:
                    return row[key]
            return default

        return cls(
            propiedad=str(pick("propiedad")).strip(),
            rol=str(pick("rol", default="atributo")).strip(),
            orden_llave=int(pick("orden_llave", "ordenLlave", default=0) or 0),
            formato_entrada=str(pick("formato_entrada", "formatoEntrada", default="texto")).strip(),
            regla_limpieza=str(pick("regla_limpieza", "reglaLimpieza", default="")).strip(),
            unidad_esperada=str(pick("unidad_esperada", "unidadEsperada", default="")).strip(),
            parametro=str(pick("parametro", default="")).strip(),
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
