# Tablon de Edictos - Ayuntamiento de Zaragoza

Research date: 2026-04-28
Researcher: R1 agent

---

## 1. URL and Access Point

**Main HTML page:**
```
https://www.zaragoza.es/sede/servicio/tablon-edicto/
```

**JSON API endpoint (open data, no auth required):**
```
https://www.zaragoza.es/sede/servicio/tablon-edicto.json
```

**Individual record:**
```
https://www.zaragoza.es/sede/servicio/tablon-edicto/{id}.json
```

**Attached PDF document:**
```
https://www.zaragoza.es/sede/servicio/tablon-edicto/{id}/document
```

**Open Data catalog entry:**
```
https://www.zaragoza.es/sede/portal/datos-abiertos/servicio/catalogo/1780
```

**Swagger/API docs:**
```
https://www.zaragoza.es/docs-api_sede/
```

**API documentation:**
```
https://www.zaragoza.es/sede/portal/datos-abiertos/api
https://www.zaragoza.es/sede/portal/espacio-de-datos/herramientas/api-rest
```


## 2. Technical Access Method

### The API is the primary access method. No scraping needed.

The Ayuntamiento de Zaragoza provides a **fully public REST JSON API** as part of its
open-data initiative. No API key, authentication, or session cookies are required.

**A simple HTTP GET with `requests` or `httpx` is sufficient.** No headless browser needed.

The HTML page is server-rendered (jQuery on the frontend, standard form submissions
for filtering). However, the JSON API is far superior for programmatic access.

### Supported response formats

Append the format extension to the base URL:
- `.json` -- JSON (primary for our use case)
- `.csv` -- CSV
- `.xml` -- XML
- `.jsonld` -- JSON-LD
- `.rdf` -- RDF/XML
- `.turtle` -- Turtle
- `.n3` -- N3

### Example requests

```bash
# All currently active herederos records
curl "https://www.zaragoza.es/sede/servicio/tablon-edicto.json?tipo.id=29&rows=50"

# Single record detail
curl "https://www.zaragoza.es/sede/servicio/tablon-edicto/4150.json"

# Download attached PDF
curl -o edicto.pdf "https://www.zaragoza.es/sede/servicio/tablon-edicto/4150/document"

# All active edictos (any category), sorted newest first
curl "https://www.zaragoza.es/sede/servicio/tablon-edicto.json?rows=50&sort=publicationDate%20desc"

# Count only (no records returned)
curl "https://www.zaragoza.es/sede/servicio/tablon-edicto.json?rows=0"
# Returns: {"totalCount":823,"start":0,"rows":0}
```


## 3. How Inheritance Records Appear

### Category filter

Inheritance-related records fall under **tipo.id = 29**, titled:
**"Acta de notoriedad/Declaracion de herederos"**

This single category covers three distinct sub-types of records:

| Sub-type | Example title pattern | Author pattern |
|----------|----------------------|----------------|
| Acta de notoriedad / Declaracion de herederos abintestato | "Acta notoriedad para declaracion herederos abintestato, por el fallecimiento de DON..." | "NOTARIO: [name]" |
| Herencia yacente (vacant estate) | "Herencia Yacen de D. [name]" | Entity name (e.g., "GESERLOCAL SL") |
| Declaracion de fallecimiento | "Declaracion de fallecimiento de D. [name]" | "SECCION CIVIL DEL TRIBUNAL DE INSTANCIA DE ZARAGOZA" |

### Current volume (as of 2026-04-28)

- **4 active records** under tipo.id=29
- **823 total active records** across all categories
- Records appear to be purged once their `expirationDate` passes (no 2025 records found)

### Key observation about sub-types

Not all records under tipo.id=29 are equally useful:
- **Acta de notoriedad records** (from notaries) are the core lead source -- these are
  the actual intestate heir declarations where property will change hands.
- **Herencia yacente records** are tax-enforcement notices for estates with no known
  heir -- may involve seized/embargoed property.
- **Declaracion de fallecimiento** records are court death declarations (person declared
  dead after disappearance) -- less relevant.


## 4. Where the Referencia Catastral Appears

### CRITICAL FINDING: The referencia catastral does NOT appear in the tablon data.

After examining all 4 current herederos records (both JSON metadata and PDF documents),
the referencia catastral is **absent** from:

1. **The JSON API fields** -- The record schema contains only:
   - `id` (string) -- internal record ID
   - `title` (string) -- free-text title with deceased name
   - `description` (string, optional) -- rarely present, contains reference numbers
   - `author` (string) -- notary name or issuing entity
   - `publicationDate` (ISO datetime)
   - `expirationDate` (ISO datetime)
   - `status` (integer) -- observed value: 3 (meaning "active/published")
   - `tipo` (object) -- `{id: int, title: string}`
   - `document` (URL) -- link to the attached PDF

2. **The PDF documents** -- Examined 2 PDFs in detail:
   - **Record 4150** (notary edicto): Contains only the deceased's name, birthplace, death
     date/place, civil status, heir name, notary address, and a 30-day claim window notice.
     **No property addresses. No cadastral references.**
   - **Record 4136** (herencia yacente): Contains a tax enforcement notice from Navarra
     with the deceased's name, death date, last domicile, and an "apremio" reference.
     **No property addresses. No cadastral references.**

### What the edictos DO contain (extractable data)

From the JSON metadata alone:
- **Deceased person's full name** (in the `title` field)
- **Notary name and location** (in the `author` field)
- **Publication window** (`publicationDate` / `expirationDate`)

From the PDF documents (requires PDF parsing):
- Deceased person's full name
- Place and date of birth
- Place and date of death
- Civil status (soltero, casado, etc.)
- Heir name(s) and relationship
- Notary's office address

### Implications for the pipeline

The tablon is valuable as a **lead-discovery** source (who died, who inherits), but it
cannot directly provide property identification. A second data source is needed to map
deceased persons to their properties. See Section 7 for proposed alternatives.


## 5. Pagination and Filtering

### Pagination

The API uses offset-based pagination with two parameters:

| Parameter | Type | Default | Max | Description |
|-----------|------|---------|-----|-------------|
| `start` | int | 0 | -- | Zero-based offset of first record |
| `rows` | int | 50 | 500 | Number of records to return |

The response always includes `totalCount` for calculating total pages.

```python
# Pagination example
import math

total = response["totalCount"]
page_size = 50
total_pages = math.ceil(total / page_size)

for page in range(total_pages):
    url = f"https://www.zaragoza.es/sede/servicio/tablon-edicto.json?tipo.id=29&start={page * page_size}&rows={page_size}"
```

### Filtering

**By category (tipo):**
```
?tipo.id=29           # Herederos declarations
```

**By date (FIQL syntax):**
```
?q=publicationDate=ge=2026-03-01T00:00:00
?q=publicationDate=le=2026-04-30T00:00:00
```

**By text in title (FIQL wildcard):**
```
?q=title==*herederos*
?q=title==*abintestato*
```

**Sorting:**
```
?sort=publicationDate desc    # newest first
?sort=publicationDate asc     # oldest first
```

**Field selection:**
```
?fl=id,title,publicationDate  # only return specific fields
```

**Combined example:**
```
https://www.zaragoza.es/sede/servicio/tablon-edicto.json?tipo.id=29&rows=50&sort=publicationDate%20desc&q=publicationDate=ge=2026-04-01T00:00:00
```

### Data retention

The API only returns **currently active** edictos (those whose `expirationDate` has not
passed). Typical publication windows are **30 days**. Once expired, records disappear
from the API. There is no historical archive accessible through this endpoint.

**This means the scraper must poll regularly (at least weekly) to capture all records
before they expire.**

### HTML page pagination (for reference)

The HTML page uses `?&start=N` with increments of 50:
```
/?&start=0    (page 1)
/?&start=50   (page 2)
/?&start=100  (page 3)
```


## 6. Rate Limits and Anti-Bot Measures

### No rate limits or anti-bot measures observed.

- No `robots.txt` restrictions on the `/sede/servicio/` path
- No CAPTCHA or JavaScript challenge
- No API key requirement
- No `X-RateLimit-*` headers in responses
- No `Retry-After` headers
- No WAF fingerprinting (Cloudflare, Akamai, etc.)
- Standard `Server` headers from the city's infrastructure

The API is explicitly designed for open-data reuse and is documented on the city's
datos-abiertos (open data) portal.

### Recommended politeness

Despite no enforced limits, we should:
- Add a 1-2 second delay between requests
- Use a descriptive `User-Agent` header
- Cache responses (the API supports ETags / If-None-Match for 304 responses)
- Avoid hammering during business hours

### License

The data is published under the Zaragoza open data license:
https://www.zaragoza.es/sede/portal/aviso-legal#condiciones


## 7. Alternative Strategy for Referencia Catastral

Since the tablon does NOT contain cadastral references, the pipeline needs a
two-stage approach:

### Stage 1: Lead Discovery (Tablon de Edictos)

Poll the tablon API for new herederos records. Extract:
- Deceased person's full name (from `title` field via regex)
- Heir name(s) (from PDF text via pdfplumber/PyMuPDF)
- Notary name (from `author` field)
- Death date/place (from PDF text)

### Stage 2: Property Lookup (Catastro or other source)

Cross-reference the deceased person's name against property records.

**Option A: Sede Electronica del Catastro - Heir Certificate**
- URL: https://www.sedecatastro.gob.es/Accesos/SecAccCertificadoHeredero.aspx
- Provides "Certificado literal de los inmuebles del fallecido"
- REQUIRES authentication (digital certificate) and proof of heir status
- NOT automatable for our use case

**Option B: Catastro Free Web Services (public, no auth)**
- Base: `http://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx`
- Methods: `Consulta_DNPRC` (by ref), `Consulta_DNPLOC` (by address), `Consulta_DNPPP` (by parcel)
- LIMITATION: Cannot search by owner name. Only by address, ref, or coordinates.
- Useful IF we already have the property address, but we don't get that from the tablon.

**Option C: Registro de la Propiedad (Property Registry)**
- The Registro de la Propiedad allows searching by owner name
- Online access via https://www.registradores.org/
- Requires fee per consultation (~9 EUR per nota simple)
- Could be automated with their API but has cost implications

**Option D: BOE / BOPZ cross-reference**
- The same herederos declarations are published in the BOE (Boletin Oficial del Estado)
  and BOPZ (Boletin Oficial de la Provincia de Zaragoza)
- BOE search: https://www.boe.es/buscar/
- Some BOE entries for herencias DO contain property descriptions with cadastral refs
- The Ministerio de Hacienda abintestato search:
  https://www.hacienda.gob.es/es-ES/Areas%20Tematicas/Patrimonio%20del%20Estado/Gestion%20Patrimonial%20del%20Estado/Paginas/Abintestato/BuscadorDeclaracionAbintestato.aspx
- However, this only covers cases where the State inherits (no known heirs)

**Option E: Municipal IBI tax rolls (Padrones de Gestion Tributaria)**
- The tablon also publishes "Padrones de Gestion Tributaria" (tipo varies)
- These might contain property owner information
- Would need to cross-reference deceased names against IBI roll entries

### Recommended approach

1. **Primary pipeline**: Tablon API (tipo.id=29) -> extract deceased name -> PDF parsing
   for heir details
2. **Property enrichment**: Use the deceased name to search the Registro de la Propiedad
   for associated properties with cadastral references
3. **Fallback**: Monitor BOE for the same declarations which sometimes include more
   property detail


## 8. API Record Schema Reference

```typescript
interface TablonEdictoRecord {
  id: string;                    // e.g. "4150"
  title: string;                 // Free text, contains deceased name
  description?: string;          // Optional, rare -- seen in herencia yacente
  author: string;                // Notary name or issuing entity
  publicationDate: string;       // ISO 8601: "2026-04-29T00:10:01"
  expirationDate?: string;       // ISO 8601: "2026-05-29T00:00:00" (optional)
  status: number;                // Observed: 3 = active
  tipo: {
    id: number;                  // Category ID (29 for herederos)
    title: string;               // Category label
  };
  document: string;              // URL to attached PDF
}

interface TablonEdictoListResponse {
  totalCount: number;            // Total matching records
  start: number;                 // Current offset
  rows: number;                  // Requested page size
  result: TablonEdictoRecord[];  // Array of records
}
```


## 9. Complete Category List (tipo IDs)

The following categories are available in the tablon filter. Tipo IDs were observed
from the API; not all IDs are known -- the list below shows the categories visible
in the HTML dropdown. The one confirmed ID is **29** for herederos.

| Category | Relevance to NadiaAI |
|----------|---------------------|
| Acta de notoriedad/Declaracion de herederos (id=29) | PRIMARY -- direct lead source |
| Declaraciones de herederos | May overlap with id=29 |
| Expropiaciones | Properties changing hands via expropriation |
| Subastas | Auctioned properties |
| Subastas otras entidades | External auctions |
| Planeamiento urbanistico (aprobacion definitiva) | Area development plans |
| Planeamiento urbanistico (aprobacion inicial) | Area development plans |
| Licitaciones publicas para enajenacion de suelo | Public land sales |
| Gestion Patrimonio | Municipal property management |
| Inmatriculaciones art. 205 Rglto. Hipotecario | First-time property registration |
| Reanudaciones de tracto | Title chain restoration |
| Concesion de bienes municipales | Municipal concessions |
| Convenios urbanisticos | Urban planning agreements |
| Licencias ambientales de actividad clasificada | Environmental permits |
| Transmision bienes funerarios | Funeral property transfers |
| (All others) | Low relevance |


## 10. Sample Regex Patterns for Data Extraction

### Extracting deceased name from JSON title field

```python
import re

# Pattern 1: "por el fallecimiento de DON/DONA [NAME]"
pat_fallecimiento = re.compile(
    r'fallecimiento\s+de\s+(DON|DO[ÑN]A)\s+(.+)',
    re.IGNORECASE
)

# Pattern 2: "declaracion de herederos abintestato de DON/DONA [NAME]"
pat_abintestato = re.compile(
    r'abintestato\s+de\s+(DON|DO[ÑN]A)\s+(.+)',
    re.IGNORECASE
)

# Pattern 3: "Herencia Yacen[te] de D./Da. [NAME]"
pat_herencia = re.compile(
    r'[Hh]erencia\s+[Yy]acen\w*\s+de\s+D[a.]?\.\s*(.+)',
    re.IGNORECASE
)

# Pattern 4: "Declaracion de fallecimiento de D./Da. [NAME]"
pat_decl_fallecimiento = re.compile(
    r'fallecimiento\s+de\s+D[a.]?\.\s*(.+)',
    re.IGNORECASE
)
```

### Extracting notary name from author field

```python
pat_notario = re.compile(
    r'NOTARIO:\s*(?:D\.\s*)?(.+?)(?:\.\s*CON\s+RESIDENCIA|$)',
    re.IGNORECASE
)
```


## 11. Fixture Files

Test fixtures saved to `tests/fixtures/tablon/`:

| File | Description |
|------|-------------|
| `api_list_herederos.json` | Full API response for tipo.id=29 with 4 records |
| `api_single_record.json` | Individual record detail (id=4150) |
| `api_empty_response.json` | Empty result set (totalCount=0) |
| `api_pagination_metadata.json` | Metadata-only response (rows=0, totalCount=823) |
| `edicto_herederos_sample.txt` | Transcribed text from PDF edicto 4150 |
