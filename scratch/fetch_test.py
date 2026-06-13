import requests
from bs4 import BeautifulSoup

url = "https://defunciones.es/defunciones/valencia/carolina-avanzini-blanco/"
headers = {
    "User-Agent": "NadiaAI/1.0 (real-estate lead pipeline; polite scraper)"
}

r = requests.get(url, headers=headers, timeout=15)
print(f"Status: {r.status_code}")
soup = BeautifulSoup(r.text, "html.parser")
print("=== TEXT CONTENT ===")
print(soup.get_text(separator="\n", strip=True)[:1000])
