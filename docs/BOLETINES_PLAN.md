# Plan: cobertura nacional de boletines (más Tier A fuera de Aragón)

## El hallazgo (por qué Aragón tenía A=67 y Madrid 17)

No es un bug de tiers ni casualidad: es **asimetría de fuentes**.

- Para **Aragón** scrapeamos boletines con **extracción de dirección dedicada** —
  sobre todo **BOA** (7 patrones calle+número), más Subastas (prov. 50) e iEsquelas
  (hardcoded `/zaragoza/`). → sus leads nacen con dirección → Tier A.
- Para **el resto de España** solo teníamos **BOE (nacional)** + esquelas
  nacionales sin dirección. → leads sin dirección → Tier B.

## La corrección de fondo (lo más importante): **BOE ya cubre España**

Como bien intuías, **los edictos notariales (Declaración de herederos, Sección V) y
judiciales se publican en el BOE a nivel nacional**. Por eso Madrid/Barcelona *sí*
tienen algo de Tier A. La ventaja de BOA no era tener *más* edictos, sino
**extraer mejor la dirección**. Eso ya está portado al camino común:

- `edict_parse._extract_domicile` (notarial) y `_extract_finca` (judicial) — calle+nº.
- El prompt del LLM pide explícitamente la finca/inmueble heredado calle+número.
- Resolución de provincia canónica para Catastro (CP → nombre canónico).

→ La misma calidad de extracción de BOA se aplica ahora a **todo BOE nacional**.

## Boletines autonómicos/provinciales — rollout escalonado y **validado**

Añadir 17 scrapers a ciegas es una trampa de fiabilidad: cada boletín tiene una web
distinta, no hay API común, y el entorno cloud no puede testearlos (IP bloqueada).
Se añaden **uno a uno, validados en PEDRO**, y solo si aportan edictos **únicos** que
el BOE no traiga (mucho contenido está duplicado y el merge lo deduplica).

Prioridad por tamaño de mercado infra-representado:

| # | Boletín | CCAA | Estado |
|---|---|---|---|
| ✅ | BOE | Nacional | activo (cubre notarial+judicial de toda España) |
| ✅ | BOA | Aragón | activo (extracción de dirección de referencia) |
| ✅ | **BOCM** | Madrid | **implementado** (motor genérico) — validar endpoint en PEDRO |
| ✅ | **DOGC** | Cataluña | **implementado** (motor genérico) — validar endpoint en PEDRO |
| 3 | **BOJA** | Andalucía | pendiente (añadir config en `boletin_autonomico.py`) |
| 4 | **DOGV** | C. Valenciana | pendiente (añadir config) |
| 5 | BOCYL | Castilla y León | pendiente |
| 6 | BOPV | País Vasco | pendiente |
| 7–17 | BOPA, DOG, BORM, BOIB, BOC (Canarias/Cantabria), BON, BOR, DOE, BOPA Asturias | resto | pendiente |
| — | BOPs provinciales | por provincia | evaluar duplicación con BOE antes |

**Criterio de aceptación por fuente:** antes de dar por bueno un boletín, medir en
PEDRO (a) nº de edictos de herencia/mes y (b) % con dirección calle+número que NO
estuvieran ya en BOE. Si el solape con BOE es ~total, se desactiva (coste sin valor).

**Arquitectura (BOCM/DOGC y futuros):** un único motor genérico
`scrapers/boletin_autonomico.py` descubre los enlaces de edictos de herencia en la
búsqueda del boletín; la extracción de heredero+dirección la hace el pipeline
existente (LLM `run_heir_extraction` + `edict_parse`), igual que BOE/BOA. Añadir una
comunidad = añadir una config (`base`, `search_url`, `source`). El motor es
**defensivo**: cualquier error de red/parseo loguea y devuelve `[]`, así un endpoint
mal afinado da 0 leads pero **nunca rompe el run**. Los `search_url` de BOCM/DOGC son
best-effort y deben confirmarse contra el sitio en vivo desde PEDRO (la IP cloud no
los alcanza).

## Qué NO mueve la aguja de Tier A
- Des-hardcodear **iEsquelas/Defunciones/Rememori** a nacional: dan **nombre+ciudad
  sin dirección** → solo añaden Tier B. No crean Tier A. (Sí ayudan a cobertura de
  señal de fallecimiento, pero no al objetivo "más Tier A".)
