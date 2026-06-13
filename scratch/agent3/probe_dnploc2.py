import requests, time, re
H = {"User-Agent":"Mozilla/5.0 NadiaAI","Accept":"application/xml"}
S = requests.Session(); S.headers.update(H)
url2 = "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/Consulta_DNPLOC"

def call(params, label):
    for attempt in range(4):
        try:
            r = S.get(url2, params=params, timeout=25)
            txt = r.text
            err = re.findall(r"<des>(.*?)</des>", txt)
            pcs = re.findall(r"<pc1>(.*?)</pc1>\s*<pc2>(.*?)</pc2>", txt, re.DOTALL)
            ldt = re.findall(r"<ldt>(.*?)</ldt>", txt)
            print(f"[{label}] status={r.status_code} err={err[:1]} RCs={len(pcs)} ldt={ldt[:1]}")
            if pcs: print("   first RC:", pcs[0][0]+pcs[0][1])
            return txt
        except Exception as e:
            print(f"[{label}] retry {attempt}: {type(e).__name__}")
            time.sleep(2)
    return None

# Try variants of sigla / number for a well-known Zaragoza street: Paseo Independencia
tests = [
    {"Provincia":"ZARAGOZA","Municipio":"ZARAGOZA","Sigla":"PS","Calle":"INDEPENDENCIA","Numero":"24"},
    {"Provincia":"ZARAGOZA","Municipio":"ZARAGOZA","Sigla":"CL","Calle":"ALFONSO I","Numero":"10"},
    {"Provincia":"ZARAGOZA","Municipio":"ZARAGOZA","Sigla":"CL","Calle":"DON JAIME I","Numero":"1"},
    {"Provincia":"ZARAGOZA","Municipio":"ZARAGOZA","Sigla":"CL","Calle":"CERRADA","Numero":"29"},  # without "DOCTOR"
    {"Provincia":"ZARAGOZA","Municipio":"ZARAGOZA","Sigla":"CL","Calle":"DOCTOR CERRADA","Numero":"29","Bloque":"","Escalera":"","Planta":"","Puerta":""},
]
for t in tests:
    call(t, f"{t['Sigla']} {t['Calle']} {t['Numero']}")
    time.sleep(1)
