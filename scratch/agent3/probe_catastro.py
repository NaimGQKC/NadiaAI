import requests
H = {"User-Agent":"NadiaAI/0.1","Accept":"application/xml"}

print("=== 1. Consulta_DNPRC by RC (14-char obras RC) ===")
url = "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/Consulta_DNPRC"
# obras RC are 14-char; DNPRC wants Prov+Mun(7)+rest(7)=14, or full 20.
for rc in ["0029606XM6202G", "3708131XM6230F"]:
    try:
        r = requests.get(url, params={"Provincia":"","Municipio":"","RC":rc}, headers=H, timeout=20)
        print(f"RC={rc} status={r.status_code} len={len(r.text)}")
        print("  ", r.text[:400].replace("\n"," "))
    except Exception as e:
        print("ERR", e)

print("\n=== 2. Consulta_DNPLOC by address (the geocoder for address-only leads) ===")
# This is what we'd need: Provincia, Municipio, via type, via name, number
url2 = "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/Consulta_DNPLOC"
try:
    r = requests.get(url2, params={
        "Provincia":"ZARAGOZA","Municipio":"ZARAGOZA",
        "Sigla":"CL","Calle":"DOCTOR CERRADA","Numero":"29",
        "Bloque":"","Escalera":"","Planta":"","Puerta":""
    }, headers=H, timeout=20)
    print("status", r.status_code, "len", len(r.text))
    print(r.text[:900])
except Exception as e:
    print("ERR", e)

print("\n=== 3. Consulta_DNPPP / Callejero ValidarVia ? Try address w/ minimal params ===")
try:
    r = requests.get(url2, params={
        "Provincia":"ZARAGOZA","Municipio":"ZARAGOZA",
        "Sigla":"CL","Calle":"BALTASAR GRACIAN","Numero":"7"
    }, headers=H, timeout=20)
    print("status", r.status_code)
    print(r.text[:900])
except Exception as e:
    print("ERR", e)
