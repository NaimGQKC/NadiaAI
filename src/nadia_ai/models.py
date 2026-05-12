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
    address: str | None = Field(default=None, description="Property address extracted from source")
    petitioners: list[str] = Field(default_factory=list, description="Names of petitioners/heirs")
    # Extended extraction fields
    fecha_fallecimiento: str | None = Field(default=None, description="Death date")
    fecha_nacimiento: str | None = Field(default=None, description="Birth date")
    lugar_nacimiento: str | None = Field(default=None, description="Birth place")
    lugar_fallecimiento: str | None = Field(default=None, description="Death place")
    localidad: str | None = Field(default=None, description="Last domicile city")
    juzgado: str | None = Field(default=None, description="Court or notary handling the case")
    # Phase 2: inheritance capture fields
    date_of_death: str | None = Field(default=None, description="ISO date of death (force-extracted)")
    heir_names: list[str] = Field(default_factory=list, description="LLM-extracted heir names")


class ParcelInfo(BaseModel):
    """Property data enriched from Catastro."""

    referencia_catastral: str
    address: str = ""
    neighborhood: str = ""
    m2: float | None = None
    year_built: int | None = None
    use_class: str = ""


class LeadRow(BaseModel):
    """A single row for Google Sheet / dashboard output.

    Phase 2: inheritance-focused with Plusvalía Clock urgency
    and heir contact data.
    """

    tier: str = Field(default="C", description="A/B/C/X actionability tier")
    fecha_deteccion: str = Field(description="Detection date (YYYY-MM-DD)")
    fuentes: str = Field(default="", description="Comma-separated source list")
    causante: str = ""
    heir_name: str = ""
    fecha_fallecimiento: str = ""
    days_since_death: int | None = None
    urgency_phase: str = ""
    localidad: str = ""
    direccion: str = ""
    referencia_catastral: str = ""
    m2: float | None = None
    tipo_inmueble: str = ""
    estado: str = "Nuevo"
    outreach_ok: str = "Sí"
    notas_sistema: str = ""
    notas: str = ""
    link_edicto: str = ""
    social_profile_url: str = ""
    # Enrichment fields
    subasta_activa: str = ""
    obras_recientes: str = ""

    def to_row(self) -> list[str]:
        """Convert to a list of strings for Sheet append."""
        return [
            self.tier,
            self.fecha_deteccion,
            self.fuentes,
            self.causante,
            self.heir_name,
            self.fecha_fallecimiento,
            str(self.days_since_death) if self.days_since_death is not None else "",
            self.urgency_phase,
            self.localidad,
            self.direccion,
            self.referencia_catastral,
            str(self.m2) if self.m2 is not None else "",
            self.tipo_inmueble,
            self.estado,
            self.outreach_ok,
            self.notas_sistema,
            self.notas,
            self.link_edicto,
            self.social_profile_url,
            self.subasta_activa,
            self.obras_recientes,
        ]

    @classmethod
    def sheet_headers(cls) -> list[str]:
        """Column headers for the Google Sheet (Spanish)."""
        return [
            "Tier",
            "Fecha detección",
            "Fuentes",
            "Causante",
            "Heredero principal",
            "Fecha fallecimiento",
            "Días desde fallecimiento",
            "Fase urgencia",
            "Localidad",
            "Dirección",
            "Ref. catastral",
            "m²",
            "Tipo inmueble",
            "Estado",
            "Outreach OK?",
            "Notas sistema",
            "Notas Nadia",
            "Link edicto",
            "Perfil social",
            "Subasta activa",
            "Obras recientes",
        ]
