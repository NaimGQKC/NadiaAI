# Plan: subir el nivel de la dirección precisa (calle + número)

**Objetivo:** pasar de "dirección a nivel ciudad" a **calle + número** en el máximo
de leads posible, para habilitar el **canal postal** (carta al inmueble heredado)
y la **vinculación con Catastro** (referencia catastral → m², año, uso).

Contexto: el teléfono personal del heredero **no es obtenible legalmente** en
España (sin directorio público + prohibición de llamada en frío). Por eso la
**dirección** es la vía de contacto legal y escalable. Este plan la lleva al
máximo nivel alcanzable.

---

## Fase 0 — Quick wins ya implementados (2026-06)

✅ **Parseo del último domicilio con número.** La regex antigua solo capturaba
letras → tiraba el número de la calle → Catastro (que exige número) nunca
resolvía. Ahora `_extract_domicile()` captura **calle + número (+ ciudad)**,
prioriza marcadores precisos y limpia el ruido del protocolo.

✅ **Persistir el domicilio en `leads.direccion`.** Antes se parseaba y se
tiraba. Ahora el backfill lo escribe (solo si tiene número), de modo que
`resolve_lead_addresses` puede geocodificarlo a referencia catastral.

**Impacto esperado:** los leads **notariales** (que llevan "último domicilio en…"
en el cuerpo del edicto) pasan de 0 dirección-resoluble a tener calle+número
siempre que el edicto lo incluya. Medir en el primer run con estos cambios.

---

## Fase 1 — Cobertura de parseo (1–2 semanas, coste 0, alto valor)

El edicto es la mejor fuente (gratis, legal, ya descargado). Subir el % de
extracción correcta:

1. **Más marcadores y fuentes de domicilio.**
   - Judicial (BOE-TEJU): el cuerpo a veces cita "finca sita en…", "inmueble en…",
     "vivienda sita en…". Añadir un parser de *finca* análogo al de domicilio.
   - Tablón/BOP: formatos municipales propios — añadir patrones por fuente.
2. **Normalización de la vía** (sin coste): ampliar `_VIA_SIGLA` en `catastro.py`
   (Cª, Ctra., Pza., Pje., Urb., Pol., Trav., Rda., Gta.…) y manejar
   "número/núm./nº/n.º/s/n", pisos/puertas ("3º B", "bajo", "esc. 2").
3. **Medir** `stats["address"]` por run y el ratio resuelto en Catastro
   (`resolve_lead_addresses`), para iterar con datos.

**KPI:** % de leads notariales/judiciales con `direccion` a nivel calle+número, y
% de esos que resuelven a referencia catastral.

---

## Fase 2 — Catastro como motor de normalización (1–2 semanas, coste 0)

Catastro es público y gratuito y resuelve dirección→RC→dirección normalizada.

1. **Reverse-fill desde Catastro.** Hoy resolvemos RC y paramos. Añadir: leer la
   dirección **normalizada** que devuelve Catastro (`ldt`) y escribirla de vuelta
   en `direccion` → direcciones limpias y consistentes para la carta.
2. **Reintento tolerante.** Si `lookup_rc_by_address` falla con número exacto,
   reintentar con variantes (sin piso/puerta, vía alternativa) antes de rendirse.
3. **Cache** de resoluciones (ya hay `CATASTRO_CACHE_DAYS`) para no repetir.

**Límite honesto:** Catastro NO se puede consultar por nombre/NIF del titular
(ilegal para privados). Solo por dirección o RC. Así que Catastro *normaliza* lo
que ya tenemos del edicto; no inventa la dirección que falta.

---

## Fase 3 — Casos de alto valor: Nota de Localización (mes 2–3, coste por consulta)

Para leads **judiciales** (herencia yacente) sin domicilio en el texto pero de
alto valor, la **Nota de Localización** del Registro (Servicio de Índices) da la
finca exacta por nombre/NIF.

- **Coste:** 9,02 € + IVA/consulta → solo para la cima de la lista.
- **Legalidad:** requiere interés legítimo; tramitar **vía gestoría colaboradora**
  (el agente no es parte de la sucesión). Reservar para piloto de alto valor.

---

## Fase 4 — Enriquecimiento comercial (opcional, coste por registro)

Normalizadores postales (Deyde/CASS-like) **corrigen** una dirección que ya
tienes (portal/escalera). **No inventan** un número que falta, y *appendear*
dirección desde el nombre cae en el problema RGPD (caso Equifax). Usar solo como
limpieza final del callejero, no como fuente.

---

## Lo que NO vamos a hacer (y por qué)
- **Teléfono del heredero:** sin directorio público + llamada en frío ilegal
  (opt-in desde 2023). Estructuralmente inviable. La dirección es la vía.
- **Catastro masivo por nombre/NIF:** ilegal para entidades privadas.
- **VPS como proxy/append de datos personales:** riesgo RGPD + IP datacenter
  bloqueada por las webs públicas.

## Orden recomendado
Fase 1 (gratis, mayor ROI) → Fase 2 (gratis, calidad) → medir → Fase 3 solo para
alto valor. El canal de salida es **postal → inbound consentido** (ver
`scratch/propuesta_contactabilidad.html`).
