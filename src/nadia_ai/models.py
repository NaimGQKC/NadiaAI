"""Pydantic models for data validation."""

from datetime import datetime

from pydantic import BaseModel, Field


class EdictRecord(BaseModel):
    """A single inheritance edict extracted from a source."""

    source: str = Field(description="Source identifier: 'tablon' or 'boa'")
    source_id: str = Field(description="Unique ID within the source")
    referencia_catastral: str | None = Field(
        default=None, description="Catastral reference if found"
    )
    edict_type: str = Field(default="declaracion_herederos_abintestato")
    published_at: datetime | None = Field(default=None)
    source_url: str = Field(default="")
    causante: str | None = Field(default=None, description="Deceased person's name")
    petitioners: list[str] = Field(default_factory=list, description="Names of petitioners/heirs")


class ParcelInfo(BaseModel):
    """Property data enriched from Catastro."""

    referencia_catastral: str
    address: str = ""
    neighborhood: str = ""
    m2: float | None = None
    year_built: int | None = None
    use_class: str = ""


class LeadRow(BaseModel):
    """A single row to be written to the Google Sheet.

    Note: referencia_catastral and property data (m², year, use_class) are
    often empty in v1 — the Tablón and BOA provide lead discovery (deceased
    name, location) but not property-level data. Nadia fills in property
    details manually as she works each lead.
    """

    fecha_deteccion: str = Field(description="Detection date (YYYY-MM-DD)")
    fuente: str = Field(description="Source: Tablón / BOA")
    causante: str = ""
    localidad: str = ""
    referencia_catastral: str = ""
    direccion: str = ""
    m2: float | None = None
    tipo_inmueble: str = ""
    estado: str = "Nuevo"
    notas: str = ""
    link_edicto: str = ""

    def to_row(self) -> list[str]:
        """Convert to a list of strings for Sheet append."""
        return [
            self.fecha_deteccion,
            self.fuente,
            self.causante,
            self.localidad,
            self.referencia_catastral,
            self.direccion,
            str(self.m2) if self.m2 is not None else "",
            self.tipo_inmueble,
            self.estado,
            self.notas,
            self.link_edicto,
        ]

    @classmethod
    def sheet_headers(cls) -> list[str]:
        """Column headers for the Google Sheet (Spanish)."""
        return [
            "Fecha detección",
            "Fuente",
            "Causante",
            "Localidad",
            "Ref. catastral",
            "Dirección",
            "m²",
            "Tipo inmueble",
            "Estado",
            "Notas",
            "Link edicto",
        ]
