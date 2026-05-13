import urllib.request
import urllib.parse
from datetime import datetime, UTC, timedelta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Referer": "https://www.boe.es/buscar/boe.php",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

def test_stealth():
    start_date = (datetime.now(UTC) - timedelta(days=30)).strftime("%d/%m/%Y")
    end_date = datetime.now(UTC).strftime("%d/%m/%Y")
    q_encoded = urllib.parse.quote_plus("herederos abintestato")
    start_date_enc = urllib.parse.quote(start_date)
    end_date_enc = urllib.parse.quote(end_date)
    
    url = (
        f"https://www.boe.es/buscar/boe.php?campo[0]=ORIS&dato[0][1]=1&dato[0][2]=2&dato[0][3]=3&dato[0][4]=4&dato[0][5]=5&dato[0][T]=T&operador[0]=and"
        f"&campo[1]=TITULOS&dato[1]=&operador[1]=and"
        f"&campo[2]=DEM&dato[2]=&operador[2]=and"
        f"&campo[3]=DOC&dato[3]={q_encoded}&operador[3]=and"
        f"&campo[4]=NBOS&dato[4]=&operador[4]=and"
        f"&campo[5]=NOF&dato[5]=&operador[5]=and"
        f"&operador[6]=and&campo[6]=FPU&dato[6][0]={start_date_enc}&dato[6][1]={end_date_enc}"
        f"&page_hits=50&sort_field[0]=FPU&sort_order[0]=desc"
        f"&sort_field[1]=ORI&sort_order[1]=asc"
        f"&sort_field[2]=REF&sort_order[2]=asc&accion=Buscar"
    )
    
    print(f"Testing URL: {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req) as response:
            html = response.read().decode('utf-8')
            if "Los valores de" in html:
                print("FAILED: Incorrect values")
            elif "No se han encontrado" in html:
                print("FAILED: No results found (but URL accepted)")
            else:
                print(f"SUCCESS! HTML length: {len(html)}")
                if "item" in html or "listado" in html:
                    print("Results found in HTML!")
                else:
                    print("No 'item' or 'listado' classes found in success page.")
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    test_stealth()
