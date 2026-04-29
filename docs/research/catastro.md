# Sede Electronica del Catastro -- Web Services Research

**Date:** 2026-04-28
**Author:** Research Agent R3
**Purpose:** Document Catastro public web services for property data enrichment in NadiaAI lead-generation pipeline.

---

## 1. Service Overview

The Direccion General del Catastro provides free, public SOAP/REST web services for querying **non-protected** (datos no protegidos) cadastral data. No API key, registration, or digital certificate is required for public fields.

There are two main service endpoints relevant to NadiaAI:

| Service | Endpoint | Purpose |
|---------|----------|---------|
| **OVCCallejero** | `https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx` | Property lookup by RC, address, or polygon/plot |
| **OVCCoordenadas** | `https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCoordenadas.asmx` | Coordinate lookup by RC, or reverse geocoding |
| **OVCCallejeroCodigos** | `https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejeroCodigos.asmx` | Same as OVCCallejero but using numeric codes instead of names |

All three support **SOAP**, **HTTP GET**, and **HTTP POST** bindings.

WSDL documents:
- `https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx?WSDL`
- `https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCoordenadas.asmx?WSDL`
- `https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejeroCodigos.asmx?WSDL`

---

## 2. Operations

### 2.1 Consulta_DNPRC (PRIMARY -- lookup by referencia catastral)

**SOAP Action:** `http://tempuri.org/OVCServWeb/OVCCallejero/Consulta_DNPRC`

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `Provincia` | string | No* | Province name (e.g., "ZARAGOZA") |
| `Municipio` | string | No* | Municipality name (e.g., "ZARAGOZA") |
| `RC` | string | Yes | Referencia catastral (14 or 20 characters) |

*Province and Municipality can be left empty when using a 20-character RC. With a 14-character RC, the response returns a list of all sub-properties (inmuebles) at that finca.

**Behavior with 14-char vs 20-char RC:**
- **14-char RC** (e.g., `6533409XM7163C`): Returns a list of all inmuebles (units) at that finca, with their RC components but WITHOUT detailed property data (no m2, year, use). The `control/cudnp` field shows the count.
- **20-char RC** (e.g., `6533409XM7163C0010OT`): Returns full property detail for that specific inmueble, including m2, year built, use class, and construction breakdown.

**This means our pipeline needs the full 20-char RC.** If we only have 14 chars from an edict, we must first query with the 14-char RC to get the list, then query each individual 20-char RC.

### 2.2 Consulta_DNPLOC (lookup by address)

**SOAP Action:** `http://tempuri.org/OVCServWeb/OVCCallejero/Consulta_DNPLOC`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `Provincia` | string | Yes | Province name |
| `Municipio` | string | Yes | Municipality name |
| `Sigla` | string | No | Street type code (CL=calle, PS=paseo, AV=avenida, PZ=plaza, etc.) |
| `Calle` | string | Yes | Street name (uppercase, no accents work fine) |
| `Numero` | string | No | Street number |
| `Bloque` | string | No | Block |
| `Escalera` | string | No | Staircase |
| `Planta` | string | No | Floor |
| `Puerta` | string | No | Door |

### 2.3 Consulta_CPMRC (coordinates by RC)

**Endpoint:** OVCCoordenadas service

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `Provincia` | string | Yes | Province name |
| `Municipio` | string | Yes | Municipality name |
| `SRS` | string | Yes | Spatial Reference System (e.g., `EPSG:4326`) |
| `RC` | string | Yes | Referencia catastral (**must be 14 characters**) |

### 2.4 Consulta_RCCOOR (reverse geocoding -- RC by coordinates)

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `SRS` | string | Yes | Spatial Reference System |
| `Coordenada_X` | string | Yes | Longitude (for EPSG:4326) |
| `Coordenada_Y` | string | Yes | Latitude (for EPSG:4326) |

### 2.5 Auxiliary Operations

- **ConsultaProvincia** -- List all provinces with codes
- **ConsultaMunicipio** -- List municipalities for a province
- **ConsultaVia** -- List streets in a municipality
- **ConsultaNumero** -- List building numbers on a street
- **Consulta_DNPPP** -- Lookup by polygon/plot (rural properties)

---

## 3. HTTP GET Interface (Simplest for Integration)

The easiest integration path is the **HTTP GET** interface. No SOAP envelope needed.

### 3.1 Sample Request -- Consulta_DNPRC (full detail, 20-char RC)

```
GET https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/Consulta_DNPRC?Provincia=&Municipio=&RC=6533409XM7163C0010OT
```

**curl:**
```bash
curl -s "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/Consulta_DNPRC?Provincia=&Municipio=&RC=6533409XM7163C0010OT"
```

**Python (requests):**
```python
import requests
import xml.etree.ElementTree as ET

BASE_URL = "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx"
NS = "http://www.catastro.meh.es/"

def consulta_dnprc(ref_catastral: str) -> dict:
    """Fetch property data by referencia catastral (20 chars)."""
    resp = requests.get(
        f"{BASE_URL}/Consulta_DNPRC",
        params={"Provincia": "", "Municipio": "", "RC": ref_catastral},
        timeout=15,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    # Check for errors
    cuerr = root.find(f"{{{NS}}}control/{{{NS}}}cuerr")
    if cuerr is not None and cuerr.text == "1":
        err = root.find(f"{{{NS}}}lerr/{{{NS}}}err")
        code = err.find(f"{{{NS}}}cod").text
        desc = err.find(f"{{{NS}}}des").text
        raise ValueError(f"Catastro error {code}: {desc}")

    bi = root.find(f"{{{NS}}}bico/{{{NS}}}bi")
    debi = bi.find(f"{{{NS}}}debi")
    dt = bi.find(f"{{{NS}}}dt")
    lourb = dt.find(f".//{{{NS}}}lourb")
    direction = lourb.find(f"{{{NS}}}dir")

    return {
        "referencia_catastral": ref_catastral,
        "address": bi.find(f"{{{NS}}}ldt").text,
        "street_type": direction.find(f"{{{NS}}}tv").text,
        "street_name": direction.find(f"{{{NS}}}nv").text,
        "street_number": direction.find(f"{{{NS}}}pnp").text,
        "floor": lourb.find(f"{{{NS}}}loint/{{{NS}}}pt").text,
        "door": lourb.find(f"{{{NS}}}loint/{{{NS}}}pu").text,
        "postal_code": lourb.find(f"{{{NS}}}dp").text,
        "province": dt.find(f"{{{NS}}}np").text,
        "municipality": dt.find(f"{{{NS}}}nm").text,
        "use_class": debi.find(f"{{{NS}}}luso").text,
        "surface_m2": int(debi.find(f"{{{NS}}}sfc").text),
        "year_built": int(debi.find(f"{{{NS}}}ant").text),
        "participation_pct": debi.find(f"{{{NS}}}cpt").text,
    }
```

### 3.2 Sample Request -- Consulta_DNPRC (list mode, 14-char RC)

```
GET https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/Consulta_DNPRC?Provincia=ZARAGOZA&Municipio=ZARAGOZA&RC=6533409XM7163C
```

This returns ALL units in the building. Use this when you only have the 14-char RC from an edict.

### 3.3 Sample Request -- Consulta_DNPLOC (by address)

```
GET https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/Consulta_DNPLOC?Provincia=ZARAGOZA&Municipio=ZARAGOZA&Sigla=PS&Calle=INDEPENDENCIA&Numero=21&Bloque=&Escalera=&Planta=&Puerta=
```

### 3.4 Sample Request -- Consulta_CPMRC (coordinates)

```
GET https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCoordenadas.asmx/Consulta_CPMRC?Provincia=ZARAGOZA&Municipio=ZARAGOZA&SRS=EPSG:4326&RC=6533409XM7163C
```

---

## 4. Response Format and Field Mapping

### 4.1 XML Namespace

All responses use:
```xml
xmlns="http://www.catastro.meh.es/"
xmlns:xsd="http://www.w3.org/2001/XMLSchema"
xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
```

**The default namespace `http://www.catastro.meh.es/` is critical for parsing.** Every element lookup must use this namespace or parsing will silently return None.

### 4.2 Successful Response -- Full Detail (20-char RC)

```xml
<?xml version="1.0" encoding="utf-8"?>
<consulta_dnp xmlns="http://www.catastro.meh.es/">
  <control>
    <cudnp>1</cudnp>    <!-- count of properties -->
    <cucons>1</cucons>   <!-- count of construction elements -->
    <cucul>0</cucul>     <!-- count of cultivos (crops, rural only) -->
  </control>
  <bico>
    <bi>
      <idbi>
        <cn>UR</cn>                <!-- UR=urbano, RU=rustico -->
        <rc>
          <pc1>6533409</pc1>       <!-- parcela catastral part 1 (7 chars) -->
          <pc2>XM7163C</pc2>       <!-- parcela catastral part 2 (7 chars) -->
          <car>0010</car>          <!-- cargo/unit number (4 chars) -->
          <cc1>O</cc1>             <!-- check digit 1 -->
          <cc2>T</cc2>             <!-- check digit 2 -->
        </rc>
      </idbi>
      <dt>
        <loine>
          <cp>50</cp>              <!-- province code (INE) -->
          <cm>297</cm>             <!-- municipality code (INE) -->
        </loine>
        <cmc>900</cmc>             <!-- municipality code (Catastro internal) -->
        <np>ZARAGOZA</np>          <!-- province name -->
        <nm>ZARAGOZA</nm>          <!-- municipality name -->
        <locs>
          <lous>
            <lourb>                <!-- urban location -->
              <dir>
                <cv>440</cv>       <!-- street code -->
                <tv>PS</tv>        <!-- street type (PS=paseo, CL=calle, etc.) -->
                <nv>INDEPENDENCIA</nv>  <!-- street name -->
                <pnp>21</pnp>     <!-- street number -->
              </dir>
              <loint>
                <pt>02</pt>        <!-- floor (planta) -->
                <pu>DR</pu>        <!-- door (puerta): DR=derecha, IZ=izquierda, CN=centro -->
              </loint>
              <dp>50001</dp>       <!-- postal code -->
              <dm>2</dm>           <!-- district -->
            </lourb>
          </lous>
        </locs>
      </dt>
      <ldt>PS INDEPENDENCIA 21 Pl:02 Pt:DR 50001 ZARAGOZA (ZARAGOZA)</ldt>  <!-- formatted address -->
      <debi>
        <luso>Residencial</luso>   <!-- USE CLASS -->
        <sfc>323</sfc>             <!-- SURFACE AREA in m2 -->
        <cpt>3,269000</cpt>        <!-- participation quota (Spanish decimal: comma) -->
        <ant>1953</ant>            <!-- YEAR BUILT -->
      </debi>
    </bi>
    <lcons>                        <!-- construction breakdown -->
      <cons>
        <lcd>VIVIENDA</lcd>        <!-- construction use (VIVIENDA, ALMACEN, OFICINA, etc.) -->
        <dt>
          <lourb>
            <loint>
              <pt>02</pt>
              <pu>DR</pu>
            </loint>
          </lourb>
        </dt>
        <dfcons>
          <stl>323</stl>           <!-- surface of this construction element -->
        </dfcons>
      </cons>
    </lcons>
  </bico>
</consulta_dnp>
```

### 4.3 Second Example -- Casco Viejo Property

**RC:** `6836504XM7163F0003QT` (Calle Mayor 8, 1A, Zaragoza)

```
Address:     CL MAYOR 8 Pl:01 Pt: A 50001 ZARAGOZA (ZARAGOZA)
Use class:   Residencial
Surface:     64 m2
Year built:  1987
Construction breakdown:
  - VIVIENDA: 53 m2 (floor 01, door A)
  - ELEMENTOS COMUNES: 11 m2
```

### 4.4 Field Mapping to NadiaAI Data Model

| NadiaAI Field | XML Path (from `bico/bi`) | Example Value |
|---------------|---------------------------|---------------|
| `address` | `ldt` (text) | "PS INDEPENDENCIA 21 Pl:02 Pt:DR 50001 ZARAGOZA (ZARAGOZA)" |
| `street_type` | `dt/locs/lous/lourb/dir/tv` | "PS" |
| `street_name` | `dt/locs/lous/lourb/dir/nv` | "INDEPENDENCIA" |
| `street_number` | `dt/locs/lous/lourb/dir/pnp` | "21" |
| `floor` | `dt/locs/lous/lourb/loint/pt` | "02" |
| `door` | `dt/locs/lous/lourb/loint/pu` | "DR" |
| `postal_code` | `dt/locs/lous/lourb/dp` | "50001" |
| `province` | `dt/np` | "ZARAGOZA" |
| `municipality` | `dt/nm` | "ZARAGOZA" |
| `m2` | `debi/sfc` | 323 (integer) |
| `year_built` | `debi/ant` | 1953 (integer) |
| `use_class` | `debi/luso` | "Residencial" |
| `participation` | `debi/cpt` | "3,269000" (Spanish decimal!) |
| `property_type` | `idbi/cn` | "UR" (urbano) |
| `district` | `dt/locs/lous/lourb/dm` | "2" |

### 4.5 Coordinates Response

```xml
<consulta_coordenadas xmlns="http://www.catastro.meh.es/">
  <control>
    <cucoor>1</cucoor>
    <cuerr>0</cuerr>
  </control>
  <coordenadas>
    <coord>
      <pc>
        <pc1>6533409</pc1>
        <pc2>XM7163C</pc2>
      </pc>
      <geo>
        <xcen>-0.881920839844364</xcen>   <!-- longitude -->
        <ycen>41.6505449874589</ycen>      <!-- latitude -->
        <srs>EPSG:4326</srs>
      </geo>
      <ldt>PS INDEPENDENCIA 21 ZARAGOZA (ZARAGOZA)</ldt>
    </coord>
  </coordenadas>
</consulta_coordenadas>
```

---

## 5. Error Handling

### 5.1 Error Response Structure

```xml
<consulta_dnp xmlns="http://www.catastro.meh.es/">
  <control>
    <cuerr>1</cuerr>          <!-- 1 = error present -->
  </control>
  <lerr>
    <err>
      <cod>5</cod>             <!-- error code -->
      <des>NO EXISTE NINGUN INMUEBLE CON LOS PARAMETROS INDICADOS</des>
    </err>
  </lerr>
</consulta_dnp>
```

### 5.2 Known Error Codes

| Code | Description (Spanish) | Meaning |
|------|----------------------|---------|
| 4 | LA REFERENCIA CATASTRAL NO ESTA CORRECTAMENTE FORMADA | Malformed RC -- check length and format |
| 5 | NO EXISTE NINGUN INMUEBLE CON LOS PARAMETROS INDICADOS | No property found for given parameters |
| 11 | LA PROVINCIA ES OBLIGATORIA | Province is required (for some operations) |
| 18 | LA REFERENCIA CATASTRAL DEBE SER DE 14 POSICIONES | RC must be exactly 14 chars (coordinates endpoint) |
| 33 | LA VIA NO EXISTE | Street not found in municipality |
| 43 | EL NUMERO NO EXISTE | Street number not found |

### 5.3 Detection Logic

```python
def has_error(root, ns="http://www.catastro.meh.es/"):
    cuerr = root.find(f"{{{ns}}}control/{{{ns}}}cuerr")
    return cuerr is not None and cuerr.text == "1"
```

---

## 6. Public vs Protected Fields

### 6.1 Public (datos no protegidos) -- NO authentication needed

These are the fields returned by the services documented above:

- Full address (street, number, floor, door, postal code)
- Surface area (m2)
- Year of construction
- Property use class (Residencial, Comercial, Industrial, etc.)
- Construction breakdown (VIVIENDA, ALMACEN, OFICINA, etc.)
- Participation quota
- Geographic coordinates
- Property type (urban/rural)
- Province, municipality, district codes

**All of these are sufficient for NadiaAI's lead enrichment.**

### 6.2 Protected (datos protegidos) -- requires digital certificate

These fields require authentication via Spanish digital certificate (certificado digital, DNIe, or FNMT):

- **Titular name** (property owner identity)
- **Valor catastral** (cadastral/tax value)
- **NIF/CIF** of the owner
- Detailed valuation breakdown

Protected data services use a different endpoint with WCF technology and require client certificate authentication. **NadiaAI does not need these fields.**

---

## 7. Rate Limits and Usage Policy

### 7.1 No Formal Rate Limit Documentation

The Catastro does **not publish explicit rate limits** for the public web services. Based on community experience and testing:

- The service is intended for **individual queries**, not bulk scraping.
- There is no API key or registration mechanism -- it is truly open.
- The service runs on legacy ASP.NET infrastructure that can be slow (typical response times 500ms-2s).
- Aggressive concurrent requests may result in temporary IP blocks or HTTP 503 responses.

### 7.2 Recommended Approach for NadiaAI

- Add **1-2 second delays** between consecutive requests.
- Implement **exponential backoff** on HTTP errors (503, 429, timeout).
- Cache results aggressively -- property data changes very rarely (annual updates).
- For our use case (enriching ~5-20 leads per week from inheritance edicts), rate limits will never be an issue.
- Set a **15-second timeout** per request.

### 7.3 Availability

- Service is generally available 24/7 but can have maintenance windows.
- No SLA is published for these free services.
- The HTTPS endpoint is the correct one to use; HTTP also works but redirects.

---

## 8. Referencia Catastral Format

### 8.1 Structure

A referencia catastral has 20 characters total:

```
6533409 XM7163C 0010 OT
|-----| |-----| |--| ||
  pc1     pc2    car cc
```

| Part | Length | Description |
|------|--------|-------------|
| `pc1` | 7 | Finca identifier (plot) |
| `pc2` | 7 | Map sheet and section |
| `car` | 4 | Sequential unit number within finca (0001, 0002, etc.) |
| `cc1` + `cc2` | 2 | Check digits |

The **14-character RC** is `pc1 + pc2` (the finca/plot reference). This identifies a building/plot but not a specific unit within it.

### 8.2 Implications for NadiaAI

Inheritance edicts typically contain the **14-character RC** (the finca). Our enrichment pipeline should:

1. Query with the 14-char RC to get the list of all units.
2. If we know the floor/door from the edict, match it to find the specific 20-char RC.
3. Query with the 20-char RC to get full property details (m2, year, use).
4. If we don't know the specific unit, we can still get the address and coordinates from the 14-char query, then present all units as candidates.

---

## 9. Street Type Codes (Sigla)

Common street type codes used in Catastro queries:

| Code | Meaning |
|------|---------|
| CL | Calle |
| PS | Paseo |
| AV | Avenida |
| PZ | Plaza |
| CM | Camino |
| CR | Carretera |
| TR | Travesia |
| RD | Ronda |
| BO | Barrio |
| UR | Urbanizacion |

---

## 10. Gotchas and Implementation Notes

### 10.1 XML Namespace is Mandatory

The default namespace `http://www.catastro.meh.es/` must be used in all XPath queries. Without it, `find()` and `findall()` return `None` silently. This is the most common source of bugs.

```python
# WRONG -- will return None
root.find("bico/bi/debi/sfc")

# CORRECT
NS = "http://www.catastro.meh.es/"
root.find(f"{{{NS}}}bico/{{{NS}}}bi/{{{NS}}}debi/{{{NS}}}sfc")
```

### 10.2 Spanish Decimal Separator

The `cpt` (participation) field uses a **comma** as decimal separator: `"3,269000"`. Parse with:
```python
float(value.replace(",", "."))
```

### 10.3 Encoding

Responses use `utf-8` encoding. Error messages contain Spanish characters (tildes, enyes). The XML declaration specifies `encoding="utf-8"`.

### 10.4 Floor Values

The `pt` (planta/floor) field can contain:
- Positive numbers: `01`, `02`, etc. (upper floors)
- Negative numbers: `-1`, `-2` (basements)
- Special values: `SS` (sotano/basement), `BJ` (bajo/ground floor), `EN` (entresuelo/mezzanine), `AT` (atico/attic)

### 10.5 The 14-char vs 20-char RC Behavior Difference

This is the most important gotcha. With a 14-char RC, `Consulta_DNPRC` returns a **list** response (multiple `rcdnp` elements under `lrcdnp`) with minimal data per entry. With a 20-char RC, it returns a **detail** response (`bico` element) with full property data. The XML structure is completely different between the two modes.

### 10.6 Empty Parameters

For HTTP GET, empty parameters must still be present in the URL:
```
?Provincia=&Municipio=&RC=...
```
Omitting them entirely may cause errors.

### 10.7 HTTPS vs HTTP

The official endpoint uses `http://` in the WSDL, but `https://` works and should be preferred. The actual host resolves correctly on both.

### 10.8 Street Name Matching

Street names in the Catastro may not match common usage:
- "DON JAIME I" might be stored as "JAIME I" or "DON JAIME"
- Use `ConsultaVia` to look up the exact street name first if `Consulta_DNPLOC` returns error 33.

### 10.9 luso (Use Class) Values

Common values for the `luso` field:

| Value | Description |
|-------|-------------|
| Residencial | Residential dwelling |
| Almacen-Estacionamiento.Uso Residencial | Storage/parking attached to residential |
| Comercial | Commercial use |
| Industrial | Industrial use |
| Oficinas | Offices |
| Cultural | Cultural/educational |
| Religioso | Religious |
| Sanidad y Beneficencia | Healthcare |
| Suelo sin edificar | Undeveloped land |

### 10.10 Construction Element Types (lcd)

| Value | Description |
|-------|-------------|
| VIVIENDA | Dwelling/apartment |
| ALMACEN | Storage/warehouse |
| OFICINA | Office |
| COMERCIO | Retail |
| GARAJE | Garage |
| ELEMENTOS COMUNES | Common areas (shared m2) |
| INDUSTRIAL | Industrial |

---

## 11. Two-Step Enrichment Pipeline

Given that edicts provide 14-char RCs, our recommended pipeline:

```python
def enrich_property(rc_14: str) -> list[dict]:
    """
    Two-step enrichment from a 14-char referencia catastral.
    Returns list of property dicts (one per unit in the building).
    """
    # Step 1: Get list of all units at this finca
    resp = requests.get(
        f"{BASE_URL}/Consulta_DNPRC",
        params={"Provincia": "", "Municipio": "", "RC": rc_14},
        timeout=15,
    )
    root = ET.fromstring(resp.content)
    if has_error(root, NS):
        return []

    # Extract all 20-char RCs from list response
    units = []
    for rcdnp in root.findall(f".//{{{NS}}}rcdnp"):
        rc = rcdnp.find(f"{{{NS}}}rc")
        rc20 = (
            rc.find(f"{{{NS}}}pc1").text
            + rc.find(f"{{{NS}}}pc2").text
            + rc.find(f"{{{NS}}}car").text
            + rc.find(f"{{{NS}}}cc1").text
            + rc.find(f"{{{NS}}}cc2").text
        )
        units.append(rc20)

    # Step 2: Fetch full details for each unit
    results = []
    for rc20 in units:
        time.sleep(1)  # rate-limit courtesy
        detail = consulta_dnprc(rc20)  # function from section 3.1
        results.append(detail)

    return results
```

---

## 12. Zaragoza-Specific Reference Data

| Field | Value |
|-------|-------|
| Province code (INE cp) | 50 |
| Municipality code (INE cm) | 297 |
| Municipality code (Catastro cmc) | 900 |
| Province name | ZARAGOZA |
| Municipality name | ZARAGOZA |

---

## 13. Summary for NadiaAI Integration

1. **No API key needed.** The public service is free and open.
2. **Use HTTP GET** for simplicity -- no SOAP envelopes required.
3. **Primary endpoint:** `https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/Consulta_DNPRC`
4. **Two-step process:** 14-char RC -> list of units -> 20-char RC -> full detail per unit.
5. **All fields we need are public:** address, m2, year built, use class.
6. **Parse XML with namespace** `http://www.catastro.meh.es/` -- this is the single biggest gotcha.
7. **Be polite with rate:** 1-2s delay between requests, cache results.
8. **Watch for Spanish decimals** in the participation field (comma, not dot).
