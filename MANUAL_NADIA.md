# Manual de NadiaAI

## Qué es NadiaAI

NadiaAI es tu asistente automático de captación de leads. Cada mañana revisa las publicaciones oficiales de múltiples fuentes — Tablón de Edictos, BOA, BOE (TEJU y Sección V), BORME, y BOP Zaragoza — buscando **declaraciones de herederos abintestato**, disoluciones de empresas y otros indicadores de que un inmueble puede salir al mercado.

Cuando encuentra una, te la envía como "lead". Un lead de herencia significa que, en los próximos 3 a 18 meses, es muy probable que uno o varios inmuebles de esa persona salgan al mercado.

**Ningún otro agente en Zaragoza monitoriza estas fuentes.** Tú ves los leads antes que nadie.

---

## Tu rutina diaria

1. **A las 08:00** recibes un email de NadiaAI:
   - Si hay leads nuevos: verás los nombres y un enlace a tu hoja de Google Sheets.
   - Si no hay novedades: un mensaje breve confirmando que el sistema revisó y no encontró nada nuevo. Esto es normal — los edictos se publican de forma irregular.

2. **Abres la hoja de Google Sheets** (el enlace está en cada email).

3. **Revisas los leads nuevos** — estarán marcados con estado "Nuevo".

4. **Investigas** los que te parezcan interesantes (ver sección siguiente).

5. **Actualizas el estado** en la hoja: "En seguimiento", "Contactado", "Descartado", etc.

---

## Cómo leer la hoja de leads

Cada fila es un lead. Las columnas son:

| Columna | Qué significa |
|---------|---------------|
| **Fecha detección** | Cuándo NadiaAI detectó el edicto |
| **Fuente** | De dónde viene: "Tablón", "BOA", "BOE", "BORME", "BOP" |
| **Causante** | Nombre de la persona fallecida — **es el dato principal** |
| **Localidad** | Ciudad (por ahora siempre Zaragoza) |
| **Ref. catastral** | Referencia catastral, si se conoce (casi siempre vacía en v1) |
| **Dirección** | Dirección del inmueble, si se conoce |
| **m²** | Metros cuadrados, si se conoce |
| **Tipo inmueble** | Residencial, comercial, etc., si se conoce |
| **Estado** | "Nuevo", "En seguimiento", "Contactado", "Descartado"... tú lo gestionas |
| **Notas** | Espacio libre para tus anotaciones |
| **Tier** | Calidad del lead: A, B, C o X (ver sección "Cómo leer la columna Tier") |
| **Fuentes** | Todas las fuentes que contribuyeron a este lead (ver sección "Qué significa la columna Fuentes") |
| **Outreach OK** | Si puedes contactar directamente o no (ver sección "Qué significa Outreach OK?") |
| **Notas outreach** | Explicación cuando el contacto está restringido |
| **Subasta activa** | Si el inmueble tiene una subasta en curso |
| **Obras recientes** | Si hay licencias de obra recientes |
| **Link edicto** | Enlace al edicto original en la web oficial |

> **Nota:** En v2, muchos leads vendrán con más información gracias a las nuevas fuentes. Aun así, algunos solo tendrán nombre del causante. Eso es normal — el valor está en detectar la herencia antes que nadie.

---

## Cómo leer la columna Tier

Cada lead tiene un "Tier" que indica su calidad y lo fácil que es trabajarlo:

| Tier | Significado | Qué hacer |
|------|-------------|-----------|
| **A** | Nombre + dirección conocidos. | Puedes enviar carta hoy. |
| **B** | Falta nombre O dirección — pero con un clic en el link puedes recuperar lo que falta. | Haz clic en el link del edicto, busca el dato que falta, y envía carta. |
| **C** | Ni nombre ni dirección directamente, pero el link funciona y puedes investigar. | Requiere algo más de trabajo, pero el lead es válido. |
| **X** | Tema de dificultad financiera (subasta, concurso). | Solo como contexto — **NO enviar carta.** |

---

## Qué significa Outreach OK?

La columna "Outreach OK" te dice si puedes contactar directamente al lead:

| Valor | Significado |
|-------|-------------|
| **Sí** | Puedes contactar por carta. La fuente es un edicto oficial de herencia. |
| **No** | No contactar directamente. Es información de contexto (subasta, concurso). Si la propiedad te llega por otro camino, puedes mencionarlo en conversación. |

---

## Qué significa la columna Fuentes

La columna "Fuentes" lista todas las fuentes que contribuyeron información a ese lead. Si un mismo caso aparece en varios boletines oficiales, verás varias fuentes separadas por comas.

**Más fuentes = más fiable.** Significa que el caso se ha confirmado desde múltiples publicaciones oficiales independientes.

Los nombres de fuente posibles son:

| Fuente | Qué es |
|--------|--------|
| **Tablón** | Tablón de Edictos del Ayuntamiento de Zaragoza |
| **BOA** | Boletín Oficial de Aragón |
| **BOE (TEJU)** | Edictos de tribunales en el BOE |
| **BOE (Sec.V)** | Edictos notariales en el BOE, Sección V |
| **BORME-I** | Actos inscritos en el Registro Mercantil (empresas) |
| **BORME-II** | Anuncios y avisos legales mercantiles |
| **BOP Zaragoza** | Boletín Oficial de la Provincia de Zaragoza |

---

## Pestaña BORME

Los leads que vienen de BORME son **leads de empresa**, no de personas individuales. Aparecen cuando:

- Un administrador de una empresa fallece
- Una empresa se disuelve y puede tener inmuebles en propiedad

**Importante:** Estos leads requieren un enfoque diferente:

- Es un contacto **B2B** (empresa a empresa), no una carta personal
- El interlocutor será el nuevo administrador, los socios restantes, o el liquidador
- El tono de la carta debe ser más formal y orientado a negocio
- No uses la plantilla de carta personal — adapta el mensaje al contexto empresarial

---

## Cómo trabajar un lead

### Paso 1: Evalúa si merece la pena

- **Haz clic en "Link edicto"** para ver el edicto original. A veces incluye el nombre del notario o detalles del acta.
- **¿Conoces el apellido?** Si es un apellido que asocias a propiedades en una zona buena, priorízalo.
- **¿Cuántos herederos hay?** Más herederos = más probabilidad de que vendan (no se ponen de acuerdo).

### Paso 2: Investiga la propiedad

- Busca el nombre del causante en el **Catastro** (catastro.gob.es) — pestaña "Consulta de datos catastrales > Titular".
- Si encuentras la finca, apunta la referencia catastral y dirección en la hoja.
- Cruza con portales (Idealista, Fotocasa) para estimar valor de mercado.

### Paso 3: Contacta a los herederos

- **Método recomendado: carta postal.** Es el canal menos intrusivo y legalmente más seguro.
- Usa la plantilla de carta que tienes más abajo.
- La carta incluye el aviso legal de protección de datos (obligatorio por la GDPR).

### Paso 4: Actualiza la hoja

Cambia el estado del lead según avances:

| Estado | Significado |
|--------|-------------|
| Nuevo | Recién detectado, sin revisar |
| En seguimiento | Estás investigando |
| Contactado | Has enviado carta |
| Respondido | Han contactado de vuelta |
| Captado | Has conseguido el encargo |
| Descartado | No interesa o no viable |

---

## Plantilla de carta

Usa esta carta para el primer contacto con los herederos. **Incluye siempre el aviso de protección de datos** (es obligatorio).

---

> **[Tu nombre]**
> **[Tu agencia inmobiliaria]**
> **[Dirección]**
> **[Teléfono] — [Email]**
>
> Zaragoza, [fecha]
>
> Estimado/a Sr./Sra. [apellido del causante]:
>
> Me dirijo a usted con motivo de un asunto que podría ser de su interés.
>
> Soy agente inmobiliaria colegiada en Zaragoza y me dedico a ayudar a familias en procesos de transmisión de inmuebles, especialmente en situaciones de herencia.
>
> Tengo entendido que su familia podría encontrarse en un proceso de estas características, y quería ofrecerle mi experiencia profesional por si en algún momento necesitan orientación sobre el valor de mercado del inmueble, los pasos para la venta, o simplemente una valoración sin compromiso.
>
> Mi servicio incluye:
> - Valoración gratuita del inmueble
> - Asesoramiento sobre el proceso de venta en herencia
> - Gestión completa de la comercialización
>
> Si le parece bien, puede contactarme en el teléfono o email que figuran arriba. Estaré encantada de atenderle sin ningún compromiso.
>
> Reciba un cordial saludo,
>
> [Tu firma]
>
> ---
>
> **INFORMACIÓN SOBRE PROTECCIÓN DE DATOS PERSONALES**
> *(Art. 14 del Reglamento General de Protección de Datos — RGPD)*
>
> **Responsable del tratamiento:** [Tu nombre completo], con domicilio en [dirección] y NIF [tu NIF].
>
> **Origen de los datos:** Sus datos (nombre y relación con el causante) se han obtenido de fuentes de acceso público: el Tablón de Edictos del Ayuntamiento de Zaragoza, el Boletín Oficial de Aragón (BOA), el Boletín Oficial del Estado (BOE), el Boletín Oficial del Registro Mercantil (BORME) y/o el Boletín Oficial de la Provincia de Zaragoza (BOP), donde se publicó la declaración de herederos abintestato o acto societario correspondiente.
>
> **Finalidad:** Ofrecerle servicios de intermediación inmobiliaria relacionados con la transmisión del inmueble objeto de la herencia.
>
> **Base jurídica:** Interés legítimo del responsable (Art. 6.1.f RGPD) en ofrecer servicios profesionales relevantes a partir de información pública.
>
> **Destinatarios:** Sus datos no se comunicarán a terceros salvo obligación legal.
>
> **Conservación:** Sus datos se conservarán durante un máximo de 24 meses desde la obtención, o hasta que ejerza sus derechos.
>
> **Derechos:** Puede ejercer sus derechos de acceso, rectificación, supresión, oposición, limitación y portabilidad dirigiéndose por escrito a la dirección indicada arriba o por email a [tu email]. Asimismo, tiene derecho a presentar reclamación ante la Agencia Española de Protección de Datos (www.aepd.es).
>
> **Si no desea recibir más comunicaciones**, responda a esta carta o envíe un email a [tu email] indicando "BAJA" y eliminaremos sus datos de forma inmediata.

---

## Marco legal en una página

NadiaAI trabaja exclusivamente con **datos públicos** de boletines oficiales del Estado:

- **Tablón de Edictos** del Ayuntamiento de Zaragoza: publicación oficial abierta a cualquier ciudadano.
- **BOA (Boletín Oficial de Aragón)**: boletín oficial de la comunidad autónoma, acceso público.
- **BOE (Boletín Oficial del Estado)**: boletín oficial nacional — secciones TEJU (edictos judiciales) y Sección V (edictos notariales).
- **BORME (Boletín Oficial del Registro Mercantil)**: publicaciones obligatorias sobre actos societarios.
- **BOP Zaragoza (Boletín Oficial de la Provincia)**: boletín oficial provincial, acceso público.

Lo que NadiaAI **sí hace:**
- Lee publicaciones oficiales abiertas al público
- Extrae el nombre del causante (persona fallecida) del edicto
- Te envía esa información para que la trabajes comercialmente
- Borra automáticamente los datos personales a los 24 meses

Lo que NadiaAI **no hace:**
- No consulta registros protegidos (Padrón, Censo Electoral, Registro Civil)
- No busca datos de herederos vivos (solo el nombre del causante, que es público)
- No envía emails ni llamadas automáticas a nadie
- No comparte datos con terceros

**Tu responsabilidad como agente:**
- Contacta siempre por **carta postal** (no email, no teléfono frío)
- Incluye siempre el **aviso de protección de datos** (Art. 14 RGPD) en la carta
- Si alguien pide que no le contactes, respeta la baja inmediatamente
- Guarda copia de las cartas enviadas por si la AEPD pregunta

---

## Si algo no funciona

| Problema | Qué hacer |
|----------|-----------|
| No recibes el email de la mañana | Revisa la carpeta de spam. Si no está ahí, avisa a [tu desarrollador]. |
| La hoja de Sheets no se actualiza | Puede que el sistema detectara 0 leads ese día (normal). Comprueba el email. |
| Ves un lead repetido | No debería pasar — el sistema filtra duplicados. Si lo ves, avisa. |
| El enlace al edicto no funciona | Los edictos del Tablón caducan a los ~30 días. Si ya pasó ese tiempo, es normal. |
| Quieres buscar en otra ciudad | Por ahora solo cubre Zaragoza capital. Díselo a tu desarrollador si quieres ampliar. |
| Veo un lead con Tier X | Es información de contexto. No envíes carta a esa persona. |
| El campo Fuentes muestra varias fuentes | Normal. Significa que el mismo caso apareció en varios boletines oficiales. Más fuentes = más fiable. |

---

*Manual generado por NadiaAI v2 — Abril 2026*
