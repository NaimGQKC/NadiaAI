import urllib.request
import urllib.parse

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# The EXACT URL from the subagent's successful search
url = "https://www.boe.es/buscar/edictos_judiciales.php?campo[0]=DOC&dato[0]=Garcia&operador[0]=and&campo[1]=DEM&dato[1]=&operador[1]=and&campo[2]=JUR&dato[2]=&operador[2]=and&campo[3]=NBO&dato[3]=&operador[4]=and&campo[4]=FPU&dato[4][0]=11%2F04%2F2026&dato[4][1]=11%2F05%2F2026&page_hits=50&sort_field[0]=FPU&sort_order[0]=desc&sort_field[1]=id&sort_order[1]=asc&accion=Buscar"

req = urllib.request.Request(url, headers=HEADERS)
try:
    with urllib.request.urlopen(req) as response:
        html = response.read().decode('utf-8')
        if "Los valores de" in html:
            print("FAILED: Still got 'incorrect values' error")
        elif "No se han encontrado" in html:
            print("SUCCESS: Connection established, but no results found (weird for Garcia)")
        else:
            print(f"SUCCESS: Found results! HTML length: {len(html)}")
            with open('boe_hardcoded_success.html', 'w', encoding='utf-8') as f:
                f.write(html)
except Exception as e:
    print(f"ERROR: {e}")
