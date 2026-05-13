import requests
for p in ['teju', 'notificaciones', 'boe_j', 'boe_n', 'edictos']:
    url = f"https://www.boe.es/datosabiertos/api/{p}/sumario/20260512"
    r = requests.get(url, headers={'Accept': 'application/xml'})
    print(f"{url}: {r.status_code}")
