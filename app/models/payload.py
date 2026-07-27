import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from collaps_engine.comparison_engine import OPERATIONS_REGISTRY

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
DEFAULT_SCHEMA_NAME = "s00001_incancer"
LEGACY_METHOD_ALIASES = {"DIFERENCIA", "IGUALDAD"}
ALLOWED_CALCULATION_METHODS = set(OPERATIONS_REGISTRY.keys()) | LEGACY_METHOD_ALIASES

def sanitize_table_identifier(value: str) -> str:
    """Extrae el nombre puro de tabla y valida que sea un identificador SQL seguro."""
    clean = value.strip()
    if "." in clean:
        clean = clean.rsplit(".", 1)[-1].strip()

    if not _IDENTIFIER_RE.match(clean):
        raise ValueError(f"Identificador SQL inválido: '{value}'")

    return clean


class AnalysisPayload(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    source: Literal["directus", "n8n"] = "directus"
    analysis_id: Optional[str] = None
    schema_name: str = DEFAULT_SCHEMA_NAME
    nombre_analisis: Optional[str] = None
    tabla_a: str
    tabla_b: str
    llave_cruce_a: str
    llave_cruce_b: str
    columnas_a: str = Field(description='Ejemplo: "cantidad" o "cantidad, precio"')
    columnas_b: str = Field(description='Ejemplo: "cantidad" o "cantidad, precio"')
    metodos_calculo: str = Field(
        description=(
            'Métodos del collaps_engine separados por coma. '
            'Ej: "math_sub, strict_equal" o legacy "DIFERENCIA, IGUALDAD"'
        )
    )
    tabla_destino: str
    callback_url: Optional[str] = None
    @field_validator("schema_name", mode="before")
    @classmethod
    def normalize_schema_name(cls, value: object) -> str:
        if value is None:
            return DEFAULT_SCHEMA_NAME
        if isinstance(value, str) and not value.strip():
            return DEFAULT_SCHEMA_NAME
        return str(value).strip()

    @field_validator("schema_name")
    @classmethod
    def validate_schema_name(cls, value: str) -> str:
        if not _IDENTIFIER_RE.match(value):
            raise ValueError(f"Identificador SQL inválido para schema_name: '{value}'")
        return value

    @field_validator("tabla_a", "tabla_b", "tabla_destino", mode="before")
    @classmethod
    def sanitize_qualified_table_names(cls, value: object) -> str:
        if value is None:
            raise ValueError("El nombre de tabla es requerido.")
        return sanitize_table_identifier(str(value))

    @field_validator("llave_cruce_a", "llave_cruce_b")
    @classmethod
    def validate_join_keys(cls, value: str) -> str:
        if not _IDENTIFIER_RE.match(value):
            raise ValueError(f"Identificador SQL inválido: '{value}'")
        return value

    @field_validator("columnas_a", "columnas_b")
    @classmethod
    def validate_column_lists(cls, value: str) -> str:
        items = [item.strip() for item in value.split(",") if item.strip()]
        if not items:
            raise ValueError("El campo no puede estar vacío.")
        for item in items:
            if not _IDENTIFIER_RE.match(item):
                raise ValueError(f"Identificador SQL inválido: '{item}'")
        return value

    @field_validator("metodos_calculo")
    @classmethod
    def validate_methods(cls, value: str) -> str:
        items = [item.strip() for item in value.split(",") if item.strip()]
        if not items:
            raise ValueError("metodos_calculo no puede estar vacío.")

        invalid: list[str] = []
        for item in items:
            normalized = item.upper() if item.upper() in LEGACY_METHOD_ALIASES else item.lower()
            if normalized not in ALLOWED_CALCULATION_METHODS:
                invalid.append(item)

        if invalid:
            raise ValueError(
                f"Métodos no soportados: {', '.join(sorted(invalid))}. "
                f"Use un method_id de collaps_engine o alias legacy DIFERENCIA/IGUALDAD."
            )
        return value

    def qualified_table_a(self) -> str:        return f"{self.schema_name}.{self.tabla_a}"

    def qualified_table_b(self) -> str:
        return f"{self.schema_name}.{self.tabla_b}"

    def qualified_destino(self) -> str:
        return f"{self.schema_name}.{self.tabla_destino}"
