"""INE (Instituto Nacional de Estadistica) calibration data fetcher.

Fetches official death and property-transfer statistics for Zaragoza province
from the INE JSON API, providing a "health dashboard" to compare pipeline
lead counts against baseline statistics and detect outages or coverage gaps.

Datasets:
  1. Estadistica de Defunciones (table 35176) — deaths by provincia
  2. MNP Defunciones mensual (table 6546) — monthly death statistics
  3. ETDP Herencias (table 3498) — property transfers via inheritance

Called weekly (Mondays 06:00) by the pipeline scheduler.
"""

import logging
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger("nadia_ai.ine")

# INE JSON API endpoints — tip=AM returns all metadata
INE_DEFUNCIONES_URL = "https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA/35176?tip=AM"
INE_MNP_DEFUNCIONES_URL = "https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA/6546?tip=AM"
INE_ETDP_HERENCIAS_URL = "https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA/3498?tip=AM"

# Zaragoza provincia code in INE datasets
ZARAGOZA_PROVINCIA_CODE = "50"

# Request configuration
REQUEST_TIMEOUT = 30  # seconds
MAX_RETRIES = 3
RETRY_BACKOFF = [2, 5, 15]  # seconds between retries

# Number of recent months to return
RECENT_MONTHS = 12

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NadiaAI-Pipeline/2.0",
    "Accept": "application/json",
})


def _fetch_with_retries(url: str) -> list[dict] | None:
    """Fetch JSON from an INE API endpoint with retry and backoff.

    Returns the parsed JSON (list of series dicts) or None on failure.
    """
    for attempt in range(MAX_RETRIES):
        try:
            resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)

            if resp.status_code >= 500:
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF[attempt]
                    logger.warning(
                        "INE %d error for %s, retrying in %ds (attempt %d/%d)",
                        resp.status_code, url, wait, attempt + 1, MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()

            resp.raise_for_status()

            data = resp.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                # Some INE endpoints wrap data in a container
                return data.get("Data", data.get("data", [data]))
            return []

        except requests.Timeout:
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF[attempt]
                logger.warning(
                    "INE timeout for %s, retrying in %ds (attempt %d/%d)",
                    url, wait, attempt + 1, MAX_RETRIES,
                )
                time.sleep(wait)
                continue
            logger.error("INE timeout after %d retries for %s", MAX_RETRIES, url)
            return None

        except requests.RequestException as e:
            logger.error("INE request failed for %s: %s", url, e)
            return None

        except ValueError as e:
            logger.error("INE returned invalid JSON for %s: %s", url, e)
            return None

    return None


def _is_zaragoza_series(series: dict) -> bool:
    """Check if an INE data series belongs to Zaragoza province (code 50).

    INE series have a 'Nombre' field and/or 'MetaData' with variable values.
    We check for provincia code "50" or the name "Zaragoza" in the metadata.
    """
    nombre = (series.get("Nombre") or "").lower()

    # Check MetaData array for provincia value
    metadata = series.get("MetaData", [])
    for meta in metadata:
        # Each meta entry may have Codigo, Nombre, Variable, etc.
        variable = meta.get("Variable", {})
        var_nombre = (variable.get("Nombre") or "").lower()

        # provincia variable
        if "provincia" in var_nombre:
            codigo = meta.get("Codigo") or ""
            if codigo == ZARAGOZA_PROVINCIA_CODE:
                return True
            meta_nombre = (meta.get("Nombre") or "").lower()
            if "zaragoza" in meta_nombre:
                return True

    # Fallback: check the series name directly
    if "zaragoza" in nombre and "50" in nombre:
        return True

    return False


def _is_herencias_series(series: dict) -> bool:
    """Check if an ETDP series specifically covers herencias (inheritance transfers).

    The ETDP table includes multiple transfer types (compraventa, herencia, donacion, etc).
    We filter for the herencia subset.
    """
    nombre = (series.get("Nombre") or "").lower()
    if "herencia" not in nombre:
        # Also check metadata for the transfer-type variable
        metadata = series.get("MetaData", [])
        for meta in metadata:
            meta_nombre = (meta.get("Nombre") or "").lower()
            if "herencia" in meta_nombre:
                return True
        return False
    return True


def _timestamp_to_month(ts_ms: int) -> str:
    """Convert INE timestamp (milliseconds since epoch) to YYYY-MM string."""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m")


def _extract_monthly_values(series_list: list[dict]) -> dict[str, float]:
    """Extract monthly values from a list of INE series matching our filters.

    Aggregates values by month (YYYY-MM). If multiple series match, sums them.
    Returns dict of {month: value}.
    """
    monthly: dict[str, float] = {}

    for series in series_list:
        data_points = series.get("Data", [])
        for point in data_points:
            valor = point.get("Valor")
            if valor is None:
                continue
            try:
                valor = float(valor)
            except (ValueError, TypeError):
                continue

            fecha = point.get("Fecha")
            if fecha is None:
                # Try alternative field names
                fecha = point.get("FK_Periodo") or point.get("Anyo")
                if fecha is None:
                    continue

            # Fecha is typically a timestamp in milliseconds
            if isinstance(fecha, (int, float)):
                month_key = _timestamp_to_month(int(fecha))
            elif isinstance(fecha, str):
                # Some endpoints return "YYYY-MM" or "YYYYMM" strings
                month_key = fecha[:7] if len(fecha) >= 7 else fecha
            else:
                continue

            monthly[month_key] = monthly.get(month_key, 0) + valor

    return monthly


def _fetch_defunciones_zaragoza() -> dict[str, float]:
    """Fetch death counts for Zaragoza province.

    Tries table 35176 (Estadistica de Defunciones) first, then falls back
    to table 6546 (MNP Defunciones mensual) if the first yields no data.
    """
    # Primary source: Estadistica de Defunciones
    data = _fetch_with_retries(INE_DEFUNCIONES_URL)
    if data:
        zaragoza_series = [s for s in data if _is_zaragoza_series(s)]
        if zaragoza_series:
            monthly = _extract_monthly_values(zaragoza_series)
            if monthly:
                logger.info(
                    "INE defunciones (table 35176): %d months for Zaragoza",
                    len(monthly),
                )
                return monthly
            logger.warning("INE table 35176: Zaragoza series found but no data points")
        else:
            logger.warning("INE table 35176: no Zaragoza series found among %d series", len(data))

    # Fallback: MNP Defunciones mensual
    logger.info("Falling back to INE MNP table 6546 for defunciones")
    data = _fetch_with_retries(INE_MNP_DEFUNCIONES_URL)
    if data:
        zaragoza_series = [s for s in data if _is_zaragoza_series(s)]
        if zaragoza_series:
            monthly = _extract_monthly_values(zaragoza_series)
            if monthly:
                logger.info(
                    "INE defunciones (table 6546): %d months for Zaragoza",
                    len(monthly),
                )
                return monthly

    logger.warning("Could not fetch defunciones data from either INE table")
    return {}


def _fetch_etdp_herencias_zaragoza() -> dict[str, float]:
    """Fetch inheritance property transfers for Zaragoza province."""
    data = _fetch_with_retries(INE_ETDP_HERENCIAS_URL)
    if not data:
        return {}

    # Filter for series that are both Zaragoza AND herencias
    matching = [s for s in data if _is_zaragoza_series(s) and _is_herencias_series(s)]
    if not matching:
        logger.warning(
            "INE ETDP table 3498: no Zaragoza+herencias series found among %d series",
            len(data),
        )
        return {}

    monthly = _extract_monthly_values(matching)
    logger.info("INE ETDP herencias: %d months for Zaragoza", len(monthly))
    return monthly


def fetch_calibration_data() -> list[dict]:
    """Fetch INE calibration data. Returns list of row dicts for the Calibracion tab.

    Each row: {
        "periodo": "2026-03",        # month
        "defunciones_zaragoza": 450,  # deaths in Zaragoza province
        "etdp_herencias_zaragoza": 120,  # inheritance transfers
        "leads_pipeline": 0,         # filled by caller
        "reach_pct": 0.0,            # filled by caller
        "outage_flag": "",           # filled by caller
    }

    Returns the most recent 12 months of data.
    Graceful on errors: logs warnings and returns empty list if INE is down.
    """
    try:
        defunciones = _fetch_defunciones_zaragoza()
        herencias = _fetch_etdp_herencias_zaragoza()
    except Exception:
        logger.exception("Unexpected error fetching INE calibration data")
        return []

    # Merge all months from both datasets
    all_months = sorted(set(defunciones.keys()) | set(herencias.keys()))

    # Take the most recent RECENT_MONTHS
    recent_months = all_months[-RECENT_MONTHS:] if len(all_months) > RECENT_MONTHS else all_months

    if not recent_months:
        logger.warning("INE calibration: no data available for any month")
        return []

    rows = []
    for month in recent_months:
        deaths = defunciones.get(month)
        etdp = herencias.get(month)
        rows.append({
            "periodo": month,
            "defunciones_zaragoza": int(deaths) if deaths is not None else None,
            "etdp_herencias_zaragoza": int(etdp) if etdp is not None else None,
            "leads_pipeline": 0,
            "reach_pct": 0.0,
            "outage_flag": "",
        })

    logger.info("INE calibration: returning %d months of data", len(rows))
    return rows


def compute_calibration_row(
    month: str,
    deaths: int | None,
    etdp_herencias: int | None,
    pipeline_leads: int,
) -> dict:
    """Build a single calibration row with computed fields.

    Args:
        month: Period string (YYYY-MM).
        deaths: Death count for Zaragoza province (from INE), or None if unavailable.
        etdp_herencias: Inheritance transfers for Zaragoza (from INE), or None.
        pipeline_leads: Number of leads discovered by the NadiaAI pipeline.

    Returns:
        Dict with all calibration fields including computed reach_pct and outage_flag.
    """
    # Compute reach percentage
    reach_pct = 0.0
    if deaths is not None and deaths > 0:
        reach_pct = round(pipeline_leads / deaths * 100, 2)

    # Compute outage flag — "POSIBLE CORTE" if deaths exist (baseline activity)
    # but pipeline_leads is zero or flat, indicating the pipeline may be down
    outage_flag = ""
    if deaths is not None and deaths > 0 and pipeline_leads == 0:
        outage_flag = "POSIBLE CORTE"

    return {
        "periodo": month,
        "defunciones_zaragoza": deaths,
        "etdp_herencias_zaragoza": etdp_herencias,
        "leads_pipeline": pipeline_leads,
        "reach_pct": reach_pct,
        "outage_flag": outage_flag,
    }
