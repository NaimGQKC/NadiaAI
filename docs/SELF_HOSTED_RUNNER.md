# Correr NadiaAI en una máquina propia (self-hosted runner)

**Por qué:** `boe.es` bloquea las IPs de datacenter de GitHub a nivel de red, así
que desde los servidores de GitHub el scraper de BOE da 0 de forma intermitente —
y BOE son los leads notariales/judiciales, los de mayor rendimiento. Una máquina
en una conexión **residencial/empresarial española** no está bloqueada.

**Bonus:** en una máquina fija la base de datos **persiste** entre ejecuciones, así
que el enriquecimiento (herederos, contactos, Catastro) por fin avanza día a día
en vez de empezar de cero cada vez (que es lo que pasa en el runner de GitHub).

Requisitos de la máquina:
- Encendida a la hora del cron (o cuando lances el run a mano).
- **Python 3.11** y **git** instalados.
- Conexión a internet española sin proxy corporativo raro.
- Puede ser el PC de Nadia, un mini-PC, una Raspberry Pi 4/5, etc. (Linux, macOS o Windows).

---

## Paso 1 — Registrar el runner en GitHub

En el navegador, en el repo:

1. **Settings → Actions → Runners → New self-hosted runner**.
2. Elige el sistema operativo de la máquina (Linux / macOS / Windows).
3. GitHub te muestra unos comandos (descargar, configurar). **No cierres esa página**, los necesitas en el paso 2.

## Paso 2 — Instalar el runner en la máquina

En la máquina (terminal), pega los comandos que te dio GitHub. Al ejecutar
`./config.sh`, cuando pregunte:

- **"Enter any additional labels"** → escribe: `nadia`
  *(Esta etiqueta es la que usa el workflow `daily-selfhosted.yml` — importante.)*
- El resto, deja los valores por defecto (Enter).

Arráncalo:

```bash
# arranque manual (para probar)
./run.sh
```

Para dejarlo permanente (que arranque solo con la máquina):

```bash
# Linux/macOS — instala el runner como servicio
sudo ./svc.sh install
sudo ./svc.sh start
```

(En Windows el instalador ofrece "Run as a service" — di que sí.)

Cuando esté conectado, en **Settings → Actions → Runners** lo verás como
**"Idle" (verde)** con la etiqueta `nadia`.

## Paso 3 — Comprobar que los secrets existen

El workflow self-hosted usa los **mismos** secrets que ya creaste (`SMTP_USER`,
`SMTP_PASSWORD`, `MAMA_EMAIL`, y opcionalmente `EXTRACTION_API_KEY`,
`PERPLEXITY_API_KEY`). No hay que volver a crearlos. **No** hace falta
`BOE_PROXY_URL` en esta máquina (la IP española no está bloqueada).

## Paso 4 — Lanzar una ejecución de prueba

En el repo: **Actions → "NadiaAI Daily Pipeline (self-hosted)" → Run workflow**,
y elige la rama. La máquina ejecutará el pipeline. Verifica que:

- Termina en verde.
- En el log de BOE aparecen registros (sin `circuit breaker tripped`).
- Llega el **correo con el Excel** a Nadia.

## Paso 5 — Activar el cron diario (y evitar duplicados)

Cuando el paso 4 funcione:

1. En `daily-selfhosted.yml`, **descomenta** el bloque `schedule:`.
2. En `daily.yml` (el de GitHub-hosted), **comenta/elimina** su `schedule:` para
   que el pipeline —y el email— **no se ejecute dos veces**.
3. Commit + push.

---

## Notas

- **Persistencia de la BD:** el workflow guarda la base en
  `~/nadia-ai-data/nadia_ai.db` (fuera del workspace, para que el `checkout` no la
  borre). Haz copia de ese fichero si quieres respaldo.
- **El dashboard:** si quieres ver los leads en el tablero Kanban en esa misma
  máquina, apúntalo a la misma BD:
  `NADIA_DB_PATH=~/nadia-ai-data/nadia_ai.db python -m nadia_ai.dashboard.app`
- **Seguridad:** un self-hosted runner ejecuta el código del repo en tu máquina.
  Como el repo es tuyo y privado, es seguro; no expongas el runner a repos
  públicos/forks.
- **Apagones:** si la máquina está apagada a la hora del cron, ese día no corre.
  Programa el cron a una hora en la que sueles tenerla encendida, o déjala
  encendida con el runner como servicio.
