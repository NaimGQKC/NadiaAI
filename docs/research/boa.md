# BOA Research: Junta Distribuidora de Herencias

Research date: 2026-04-28

## 1. BOA Online Portal

**Main URL:** https://www.boa.aragon.es/

The Boletin Oficial de Aragon (BOA) is the official gazette of the Autonomous Community of Aragon. It publishes daily (except Saturdays, Sundays, and holidays) and its complete database goes back to 1978. Content is updated at 11:00 AM daily.

### Search Interfaces

| Interface | URL | Notes |
|-----------|-----|-------|
| Advanced Search | `https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI?CMD=VERLST&BASE=BZHT&SEC=BUSQUEDA_AVANZADA` | Main CGI-based search |
| Date Search | `https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI?CMD=VERLST&BASE=BOLE&SEC=BUSQUEDA_FECHA&PUBL=YYYYMMDD` | Browse by issue date |
| Open Data | `https://www.boa.aragon.es/#/opendata/opendatabuscador` | SPA interface, iBOA XML format |
| Open Data Catalog | `https://opendata.aragon.es/datos/catalogo/dataset/boletin-oficial-aragon-diario` | Daily XML dataset since 2010 |

### CGI Search System (BRSCGI)

The BOA uses a legacy CGI system called BRSCGI. Key parameters:

```
Base URL: https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI

CMD values:
  VERLST  = list search results
  VERDOC  = view document detail (HTML metadata page)
  VEROBJ  = download document object (PDF or .sig)

BASE values:
  BZHT = advanced search database
  BOLE = date-based browsing database

Key search parameters:
  TITU     = title search (supports + for spaces)
  TEXT-C   = full-text search
  ORGA-C   = organization filter
  SECC-C   = section filter (e.g., "BOA")
  @PUBL-GE = publication date >= (YYYYMMDD)
  @PUBL-LE = publication date <= (YYYYMMDD)
  SORT     = sort order (-PUBL for newest first)
  DOCS     = result range (e.g., 1-100)
  RNG      = results per page
  SEPARADOR = separator (empty string)
```

**Effective search URL for all Junta Distribuidora announcements:**
```
https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI?CMD=VERLST&BASE=BZHT&DOCS=1-100&SEC=BUSQUEDA_AVANZADA&SORT=-PUBL&SEPARADOR=&TITU=Junta+Distribuidora+de+Herencias
```

This returns all 49 results (as of 2024) in reverse chronological order.


## 2. Publication Types

There are **two distinct types** of BOA announcements from the Junta Distribuidora de Herencias, governed by different articles of Decreto 185/2014:

### Type A: Pre-Meeting Convocatoria (Article 18)

**Title pattern:** "ANUNCIO de la Junta Distribuidora de Herencias ... relativo a la distribucion del patrimonio procedente de la sucesion legal de causantes"

**Published:** At least 1 month before the Junta meeting date.

**Content:** Lists the deceased persons (causantes) whose estates will be distributed, with:
- Causante full name (SURNAME, NAME format)
- Tipo de liquidacion (GENERAL or PARCIAL)
- Cantidad propuesta (amount in EUR)
- Localidad (municipality of last residence)

**This is the lead-generation data source** -- it identifies deceased persons and their municipalities.

### Type B: Post-Meeting Distribution (Article 24)

**Title pattern:** "ANUNCIO de la Junta Distribuidora de Herencias ... por el que se hace publica la distribucion de los patrimonios hereditarios acordada en su reunion del dia [date]"

**Published:** Shortly after the Junta meeting.

**Content:** Lists each beneficiary social organization with:
- Organization name and location
- Amount allocated
- Project description (destino)
- Origin breakdown by causante estate (caudal relicto)


## 3. Publication Cadence

Based on analysis of all 49 announcements (2002-2024):

**The Junta meets 1-2 times per year.** Each meeting produces a pair of announcements:

| Year | Convocatoria Date | Meeting Date | Distribution Published |
|------|-------------------|--------------|----------------------|
| 2024 | 2024-06-18 | 2024-09-04 | 2024-09-19 |
| 2023 | 2023-03-21 | 2023-04-28 | 2023-06-07 |
| 2022 | 2022-03-18, 2022-04-29 | 2022-06-16 | 2022-06-28 |
| 2021 | 2021-05-06, 2021-06-09 | 2021-06-22 | 2021-06-30 |
| 2019 | 2019-03-04 | 2019-04-xx | 2019-04-29 |
| 2018 | 2018-02-15 | 2018-04-xx | 2018-04-24 |
| 2017 | 2017-06-28 | 2017-09-08 | 2017-09-21 |
| 2016 | 2016-xx-xx | 2016-xx-xx | 2016-08-02 |
| 2015 | 2015-03-04 | 2015-04-15 | 2015-05-11 |

**Pattern:** Convocatoria typically Q1-Q2, meeting typically Q2-Q3. A scraper checking monthly is sufficient.

In some years (2021, 2022) there were **multiple convocatorias** before a single meeting, suggesting batched processing.


## 4. Document Format Analysis

### Format: PDF only

All BOA Junta Distribuidora announcements are published as **PDF/A-2b** files. There is no standalone HTML text version of the document content.

Each document in the search results has two associated objects:
1. **PDF file** (first MLKOB): `BRSCGI?CMD=VEROBJ&MLKOB={id}&type=pdf`
2. **Digital signature** (second MLKOB): `BRSCGI?CMD=VEROBJ&MLKOB={id}` (returns .sig file)

The VERDOC URL leads to an HTML metadata page (title, date, section) but does NOT contain the announcement text. The actual content is exclusively in the PDF.

### PDF Text Extraction

PDF text is extractable with `pdftotext` or equivalent libraries. The text encoding is Latin-1/Windows-1252 (accented characters appear as replacement characters in UTF-8 without conversion). Text is well-structured and parseable.

### Convocatoria (Type A) PDF Structure

```
Page 1:
  - Header: BOA number, date
  - Department name
  - Announcement title
  - Legal preamble (Article 18 reference)
  - Five numbered sections:
    1. Meeting date, number of causantes, total amount
    2. Reference to attached table
    3. Distribution criteria
    4. Requirements for applicant entities
    5. Application deadline (1 month from publication)
  - Signatory block
  - CSV code (e.g., BOA20230321034)

Page 2 (or end of page 1):
  - Table header: "RELACION DE CAUSANTES E IMPORTE A REPARTIR..."
  - Table with columns: Causante | Tipo de liquidacion | Cantidad propuesta | Localidad
  - CSV code
```

### Distribution (Type B) PDF Structure

```
Multiple pages:
  - Header: BOA number, date
  - Section: "b) Otros anuncios"
  - Department name
  - Announcement title (references meeting date)
  - Legal preamble (Article 24 reference)
  - For each beneficiary organization:
    - Organization name. Municipality.
    - Importe: amount EUR.
    - Destino: project description.
    - Origen: breakdown by causante estate with individual amounts.
  - Signatory block
  - CSV code
```


## 5. Critical Finding: No Referencia Catastral in BOA

**The BOA Junta Distribuidora announcements do NOT contain referencia catastral or property inventories.**

This is because of the legal process defined in Decreto 185/2014:

1. **Art. 8:** Once the estate is accepted, the Direccion General de Patrimonio prepares the inventory of assets (including real estate).
2. **Art. 10:** The DG Patrimonio registers properties in the Registro de la Propiedad, values them, and manages/sells them.
3. **Art. 12:** Real estate is liquidated (sold at public auction) BEFORE the Junta meeting.
4. **Art. 18:** The convocatoria announces only the NET CASH AMOUNT to distribute - properties have already been sold.
5. **Art. 24:** The distribution announcement allocates cash to social organizations.

The property inventory exists as an internal administrative document at the Direccion General de Patrimonio, but it is NOT published in the BOA. The BOA only receives the final monetary figures after all real estate has been liquidated.

### Where Property Data Might Be Found

Per Decreto 185/2014:
- **Art. 10.e:** Properties are registered at the Registro de la Propiedad in the name of the Comunidad Autonoma de Aragon.
- **Art. 12:** Public auctions of inherited real estate are announced - these could be published as separate BOA notices (search for "subasta" + "patrimonio").
- **Art. 8.2:** A certification from the DG Patrimonio lists the real estate included in the estate - this is an administrative document, not publicly published.
- Properties assigned to social housing (not sold) go to the General Directorate of Housing.


## 6. URL Patterns and API Access

### PDF Download Pattern

```
https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI?CMD=VEROBJ&MLKOB={mlkob_id}&type=pdf
```

The MLKOB ID is unique per document. The first of the two MLKOB IDs per search result is the PDF; the second is the digital signature.

### Search API Pattern (Programmatic)

```
GET https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI
  ?CMD=VERLST
  &BASE=BZHT
  &DOCS={start}-{end}        # e.g., 1-100
  &SEC=BUSQUEDA_AVANZADA
  &SORT=-PUBL                 # newest first
  &SEPARADOR=
  &TITU={search_terms}        # title search, + for spaces
  &@PUBL-GE={YYYYMMDD}        # date range start (optional)
  &@PUBL-LE={YYYYMMDD}        # date range end (optional)
  &SECC-C={section}            # section filter (optional)
  &TEXT-C={fulltext}           # full-text search (optional)
```

Returns HTML page with search results. Each result includes MLKOB links.

### Open Data (XML) Access

The BOA Open Data platform at `https://opendata.aragon.es/datos/catalogo/dataset/boletin-oficial-aragon-diario` provides:
- **Format:** iBOA XML
- **Coverage:** January 4, 2010 onwards
- **Update frequency:** Daily
- **License:** CC-BY 4.0
- **RDF metadata:** `https://opendata.aragon.es/ckan/dataset/boletin-oficial-aragon-diario.rdf`

This XML dataset could be an alternative to scraping the CGI search, but needs further investigation for API endpoints.

### CSV Code

Each BOA document has a CSV verification code (e.g., BOA20230321034) that can be used for authenticity verification at the BOA portal.


## 7. Rate Limits and Access Restrictions

- **No authentication required.** All BOA content is public.
- **No explicit rate limits documented.** The CGI system is a legacy application.
- **No robots.txt restrictions observed** for the EBOA/BRSCGI path.
- **Recommended approach:** Be polite -- add 1-2 second delays between requests. The search results page returns up to 100 results at once, so pagination is minimal.
- **PDF sizes:** 150-250 KB per convocatoria PDF. Distribution PDFs can be larger (up to 1.7 MB for older full-BOA-issue PDFs).
- **Encoding:** PDF text extraction produces Latin-1/Windows-1252 encoded text. Must handle encoding conversion.


## 8. Scraping Strategy for NadiaAI

### Recommended Approach

Since the BOA Junta Distribuidora announcements do NOT contain referencia catastral, the BOA is a **secondary signal source**, not a primary property-identification source.

**What BOA provides:**
1. Names of deceased persons (causantes) whose estates include/included real estate in Aragon
2. Municipality of last residence (Localidad) - useful for geographic targeting
3. Whether the liquidation is GENERAL (estate fully processed) or PARCIAL (more to come)
4. Net monetary value of the estate (proxy for property value)

**Scraping pipeline:**
1. Search BOA for new Junta Distribuidora convocatorias (monthly check is sufficient)
2. Download and parse the PDF with pdftotext
3. Extract the causante table (structured text, regex-parseable)
4. Cross-reference causante names and localities with other data sources (Catastro, Registro de la Propiedad) to find associated properties

### Alternative: Public Auction Notices

Real estate from intestate estates is sold at public auction. These auctions may be announced in the BOA as separate notices. A search for "subasta" combined with "patrimonio" or "herencia" could yield announcements that DO contain property descriptions and potentially referencia catastral. This is a separate research track worth investigating.


## 9. Legal Framework

- **Decreto 185/2014, de 18 de noviembre** -- Main regulation. BOA DOCN: 000191939. Full text at: `https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI?CMD=VERDOC&BASE=BOLE&SEC=BUSQUEDA_AVANZADA&SEPARADOR=&DOCN=000191939`
- **Codigo de Derecho Foral de Aragon, Articles 535-536** -- Statutory basis for intestate succession to the Comunidad Autonoma
- **Ley 3/2016, de 4 de febrero** -- Reform of Art. 535 CDFA
- **Ley 5/2009, de 30 de junio** -- Ley de Servicios Sociales de Aragon (beneficiary framework)

Key provisions:
- Art. 18: Convocatoria must be published at least 1 month before Junta meeting
- Art. 19: Distribution among social assistance establishments, preference to municipality of deceased's last residence
- Art. 20: If deceased died outside Aragon, last Aragonese residence applies; if unknown, where properties are located
- Art. 22: 10% bonus to entities that first reported the intestate estate
- Art. 23: Multiple estates can be processed in a single Junta session
- Art. 24: Distribution agreements must be published in the BOA


## 10. Fixtures Saved

| File | Description |
|------|-------------|
| `tests/fixtures/boa/convocatoria_2023_03_21.txt` | Pre-meeting announcement with 8 causantes |
| `tests/fixtures/boa/convocatoria_2024_06_18.txt` | Pre-meeting announcement with 4 causantes |
| `tests/fixtures/boa/distribucion_2017_09_21.txt` | Post-meeting distribution to beneficiaries |
| `tests/fixtures/boa/search_results_index.json` | Structured index of search results with URLs |
| `tests/fixtures/boa/convocatoria_table_structure.json` | Schema of the causante table with sample data |


## 11. Department Name Changes

The issuing department name has changed over the years:
- **Current (2024):** Departamento de Hacienda, Interior y Administracion Publica
- **2018-2023:** Departamento de Hacienda y Administracion Publica
- **Pre-2018:** Departamento de Economia, Hacienda y Empleo

The signing officer title also varies:
- **Current:** Directora General de Contratacion, Patrimonio y Organizacion
- **Previous:** Directora General de Patrimonio y Organizacion

This affects text-based filtering. Title-based search (`TITU=Junta+Distribuidora+de+Herencias`) is more reliable than organization-based search.
