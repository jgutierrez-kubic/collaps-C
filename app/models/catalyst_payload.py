from typing import Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from app.core.catalyst.table_contract import CatalystTableContract
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
    config_table: Optional[str] = Field(
        default=None,
        description="Tabla de reglas de ingesta (resuelta vía job o CATALYST_CONFIG_TABLE)",
    )
    boveda_table: Optional[str] = Field(
        default=None,
        description="Tabla bóveda KV SCD2 (resuelta vía job o CATALYST_BOVEDA_TABLE)",
    )
    identidad_table: Optional[str] = Field(
        default=None,
        description="Tabla de identidad UUID (resuelta vía job o CATALYST_IDENTIDAD_TABLE)",
    )
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

    @field_validator("config_table", "boveda_table", "identidad_table", mode="before")
    @classmethod
    def sanitize_optional_table_names(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return sanitize_table_identifier(str(value))

    @field_validator("separador_llave")
    @classmethod
    def validate_separador_llave(cls, value: str) -> str:
        if not value:
            raise ValueError("separador_llave cannot be empty.")
        return value

    def resolve_tables(self) -> CatalystTableContract:
        return CatalystTableContract.from_job_fields(
            config_table=self.config_table,
            boveda_table=self.boveda_table,
            identidad_table=self.identidad_table,
        )


class CatalystMaterializePayload(BaseModel):
    """Payload RMS v1.6 — materializa bóveda VIGENTE en tabla curada a_4."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        strict=True,
        extra="forbid",
    )

    source: Literal["directus", "n8n"] = "n8n"
    schema_name: str = DEFAULT_SCHEMA_NAME
    source_table: str = Field(
        description="Tabla origen (a_1) cuya configuración y bóveda se materializan"
    )
    target_table: str = Field(
        description="Tabla curada destino (a_4_*) — nombre definido por el usuario"
    )
    config_table: Optional[str] = Field(
        default=None,
        description="Tabla de reglas de ingesta (resuelta vía job o CATALYST_CONFIG_TABLE)",
    )
    boveda_table: Optional[str] = Field(
        default=None,
        description="Tabla bóveda KV SCD2 (resuelta vía job o CATALYST_BOVEDA_TABLE)",
    )
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
            raise ValueError(f"Invalid SQL identifier for schema_name: '{value}'")
        return value

    @field_validator("source_table", "target_table", mode="before")
    @classmethod
    def sanitize_required_table_names(cls, value: object) -> str:
        if value is None:
            raise ValueError("Identificador de tabla requerido.")
        return sanitize_table_identifier(str(value))

    @field_validator("config_table", "boveda_table", mode="before")
    @classmethod
    def sanitize_optional_table_names(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return sanitize_table_identifier(str(value))

    def resolve_tables(self) -> CatalystTableContract:
        return CatalystTableContract.from_job_fields(
            config_table=self.config_table,
            boveda_table=self.boveda_table,
            identidad_table=None,
        )


class CatalystSyncBackPayload(BaseModel):
    """Payload RMS v1.6 — sync-back de edición manual hacia bóveda SCD2."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        strict=True,
        extra="forbid",
    )

    source: Literal["directus", "n8n"] = "n8n"
    schema_name: str = DEFAULT_SCHEMA_NAME
    source_table: str = Field(description="Tabla origen (a_1) asociada a la entidad")
    entidad_interna_id: str = Field(
        validation_alias=AliasChoices(
            "entidad_interna_id",
            "entidadInternaId",
        ),
        description="UUID de la entidad en a_3_identidad",
    )
    propiedad: str = Field(
        validation_alias=AliasChoices(
            "propiedad",
            "propiedadOrigen",
            "propiedad_origen",
        ),
        description="Nombre de la propiedad/columna a actualizar",
    )
    nuevo_valor: str | int | float | bool | None = Field(
        validation_alias=AliasChoices(
            "nuevo_valor",
            "nuevoValor",
        ),
        description="Nuevo valor para la propiedad",
    )
    usuario_email: str = Field(
        validation_alias=AliasChoices(
            "usuario_email",
            "usuarioEmail",
            "creado_por",
            "creadoPor",
        ),
        description="Email del usuario que realiza la edición",
    )
    config_table: Optional[str] = None
    boveda_table: Optional[str] = None
    identidad_table: Optional[str] = None

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

    @field_validator("source_table", "propiedad", mode="before")
    @classmethod
    def sanitize_required_identifiers(cls, value: object) -> str:
        if value is None:
            raise ValueError("Identificador requerido.")
        return sanitize_table_identifier(str(value))

    @field_validator("entidad_interna_id", mode="before")
    @classmethod
    def validate_entidad_interna_id(cls, value: object) -> str:
        if value is None or not str(value).strip():
            raise ValueError("entidad_interna_id es requerido.")
        return str(value).strip()

    @field_validator("usuario_email")
    @classmethod
    def validate_usuario_email(cls, value: str) -> str:
        email = value.strip()
        if not email or "@" not in email:
            raise ValueError("usuario_email debe ser un email válido.")
        return email

    @field_validator("config_table", "boveda_table", "identidad_table", mode="before")
    @classmethod
    def sanitize_optional_table_names(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return sanitize_table_identifier(str(value))

    def resolve_tables(self) -> CatalystTableContract:
        return CatalystTableContract.from_job_fields(
            config_table=self.config_table,
            boveda_table=self.boveda_table,
            identidad_table=self.identidad_table,
        )
