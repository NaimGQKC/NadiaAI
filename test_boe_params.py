from nadia_ai.scrapers.boe import fetch_teju_search
from datetime import UTC, datetime, timedelta
from bs4 import BeautifulSoup
import requests

def test_search(query, dem=None):
    since = datetime.now(UTC) - timedelta(days=365)
    params = {
        "campo[0]": "DOC",
        "dato[0]": query,
        "operador[0]": "",
        "accion": "Buscar",
        "page_hits": "200",
    }
    if dem:
        params["campo[1]"] = "DEM"
        params["dato[1]"] = dem
        params["operador[1]"] = "Y"
        
    params["FechaPublicacionDesde"] = since.strftime("%d/%m/%Y")
    params["FechaPublicacionHasta"] = datetime.now(UTC).strftime("%d/%m/%Y")

    url = "https://www.boe.es/buscar/edictos_judiciales.php"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    resp = requests.get(url, params=params, headers=headers)
    print(f"Testing DEM='{dem}' -> Status: {resp.status_code}, Length: {len(resp.text)}")
    if "No se han encontrado" in resp.text:
        print("  Result: No results found.")
    else:
        soup = BeautifulSoup(resp.text, 'lxml')
        results = soup.find_all(class_='listado')
        print(f"  Result: Found {len(results)} listado elements.")
        items = soup.select('div.documento') or soup.select('li.resultado') or soup.select('.dato')
        print(f"  Items found: {len(items)}")

test_search('"declaracion de herederos"', dem="Zaragoza")
test_search('"declaracion de herederos"', dem="")
test_search('"declaracion de herederos"')
