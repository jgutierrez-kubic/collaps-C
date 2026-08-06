from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from app.models.payload import DEFAULT_SCHEMA_NAME, _IDENTIFIER_RE, sanitize_table_identifier

DEFAULT_SEPARADOR_LLAVE = "|"


class CatalystJobPayload(BaseModel):
    """Payload para el job asíncrono del refiner Catalyst (RMS Genérico v1.4)."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        strict=True,
        extra="forbid",
    )

    source: Literal["directus", "n8n"] = "n8n"
    schema_name: str = DEFAULT_SCHEMA_NAME
    source_table: str = Field(description="Tabla origen ancha (a_1) a refinar")
    callback_url: Optional[str] = None
    separador_llave: str = Field(
        default=DEFAULT_SEPARADOR_LLAVE,
        description="Separador para material de llave compuesta (es_llave)",
    )

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
            raise ValueError(f"Invalid SQL identifier for schema_name: '{value}'")
        return value

    @field_validator("source_table", mode="before")
    @classmethod
    def sanitize_source_table(cls, value: object) -> str:
        if value is None:
            raise ValueError("source_table is required.")
        return sanitize_table_identifier(str(value))

    @field_validator("separador_llave")
    @classmethod
    def validate_separador_llave(cls, value: str) -> str:
        if not value:
            raise ValueError("separador_llave cannot be empty.")
        return value
