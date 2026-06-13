import requests, time
H = {"User-Agent":"Mozilla/5.0 NadiaAI","Accept":"application/xml"}
S = requests.Session(); S.headers.update(H)

def call(url, params, label):
    for attempt in range(4):
        try:
            r = S.get(url, params=params, timeout=25)
            print(f"[{label}] status={r.status_code} len={len(r.text)}")
            return r.text
        except Exception as e:
            print(f"[{label}] attempt {attempt} err: {type(e).__name__}")
            time.sleep(3)
    return None

url2 = "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/Consulta_DNPLOC"
txt = call(url2, {
    "Provincia":"ZARAGOZA","Municipio":"ZARAGOZA",
    "Sigla":"CL","Calle":"DOCTOR CERRADA","Numero":"29",
    "Bloque":"","Escalera":"","Planta":"","Puerta":""
}, "DNPLOC Doctor Cerrada 29")
if txt:
    print(txt[:1500])
    print("..." )
    # look for RC
    import re
    pcs = re.findall(r"<pc1>(.*?)</pc1>.*?<pc2>(.*?)</pc2>", txt, re.DOTALL)
    print("RC fragments found:", pcs[:5])
    errs = re.findall(r"<des>(.*?)</des>", txt)
    print("error desc:", errs[:3])
