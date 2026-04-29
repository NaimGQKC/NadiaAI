# Manual de NadiaAI

## Qué es NadiaAI

NadiaAI es tu asistente automático de captación de leads. Cada mañana revisa las publicaciones oficiales del Ayuntamiento de Zaragoza (Tablón de Edictos) y del Boletín Oficial de Aragón (BOA) buscando **declaraciones de herederos abintestato** — es decir, personas fallecidas cuya herencia aún no se ha repartido.

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
| **Fuente** | De dónde viene: "Tablón" (Ayuntamiento) o "BOA" |
| **Causante** | Nombre de la persona fallecida — **es el dato principal** |
| **Localidad** | Ciudad (por ahora siempre Zaragoza) |
| **Ref. catastral** | Referencia catastral, si se conoce (casi siempre vacía en v1) |
| **Dirección** | Dirección del inmueble, si se conoce |
| **m²** | Metros cuadrados, si se conoce |
| **Tipo inmueble** | Residencial, comercial, etc., si se conoce |
| **Estado** | "Nuevo", "En seguimiento", "Contactado", "Descartado"... tú lo gestionas |
| **Notas** | Espacio libre para tus anotaciones |
| **Link edicto** | Enlace al edicto original en la web oficial |

> **Nota:** En la versión actual, la mayoría de leads solo tendrán nombre del causante, fuente y fecha. La referencia catastral, dirección y metros cuadrados los tendrás que investigar tú. Eso es normal — el valor está en detectar la herencia antes que nadie.

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
> **Origen de los datos:** Sus datos (nombre y relación con el causante) se han obtenido de fuentes de acceso público: el Tablón de Edictos del Ayuntamiento de Zaragoza y/o el Boletín Oficial de Aragón (BOA), donde se publicó la declaración de herederos abintestato correspondiente.
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

NadiaAI trabaja exclusivamente con **datos públicos**:

- **Tablón de Edictos** del Ayuntamiento de Zaragoza: publicación oficial abierta a cualquier ciudadano.
- **BOA (Boletín Oficial de Aragón)**: boletín oficial de la comunidad autónoma, acceso público.

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

---

*Manual generado por NadiaAI v1 — Abril 2026*
