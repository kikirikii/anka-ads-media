#!/usr/bin/env python3
"""Publica los Trial Reels aprobados de trials/cola.json en @ankaaerial.ar.

Lo corre la rutina en la nube a las 8, 12 y 21 h (Tucumán). También sirve a mano.

Reglas (no cambiar sin OK de Kiki):
  - Solo publica ítems con "aprobado": true cuya fecha y franja ya llegaron
    y que no tengan más de 12 h de atraso (lo vencido no sale solo).
  - Como máximo 2 por corrida.
  - Nunca publica dos veces: antes de subir, compara el caption con las últimas
    50 publicaciones de la cuenta. Si ya está, lo saltea.
  - Frena el ítem si el caption tiene un número de teléfono, "pionero(s)",
    "primero(s)", "ANAC", "seguro(s)" o "certificad…".
  - Siempre como trial MANUAL, con video_url, v27 y sin share_to_feed
    (la forma que Kiki confirmó en la app el 18/11/2026).

Token: variable de entorno IG_TOKEN. Nunca se escribe en archivos ni se imprime.

Uso:
    python3 trials/publicar_cola.py          # publica lo que toca
    python3 trials/publicar_cola.py --seco   # muestra qué publicaría, no publica
Solo stdlib.
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

IG_USER_ID = "17841415866199140"  # @ankaaerial.ar
GRAPH = "https://graph.facebook.com/v27.0"
RAW = "https://raw.githubusercontent.com/kikirikii/anka-ads-media/main/"
AQUI = os.path.dirname(os.path.abspath(__file__))
COLA = os.path.join(AQUI, "cola.json")
RAIZ = os.path.dirname(AQUI)
TUC = dt.timezone(dt.timedelta(hours=-3))
FRANJAS = (8, 12, 21)
MAX_POR_CORRIDA = 2
ATRASO_MAX = dt.timedelta(hours=12)
PROHIBIDO = re.compile(r"pioner|\bprimer[oa]s\b|\banac\b|\bseguros?\b|certificad", re.I)
TELEFONO = re.compile(r"(?:\+?\d[\s-]?){8,}")


def pedir(metodo, url, datos=None):
    cuerpo = urllib.parse.urlencode(datos).encode() if datos is not None else None
    req = urllib.request.Request(url, data=cuerpo, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Error {e.code} en {url.split('?')[0]}: {e.read().decode(errors='replace')[:400]}")


def normal(texto):
    return " ".join((texto or "").split())


def ya_publicados(tk):
    r = pedir("GET", f"{GRAPH}/{IG_USER_ID}/media?" + urllib.parse.urlencode(
        {"fields": "caption", "limit": 50, "access_token": tk}))
    return {normal(m.get("caption")) for m in r.get("data", [])}


def cupo(tk):
    r = pedir("GET", f"{GRAPH}/{IG_USER_ID}/content_publishing_limit?" + urllib.parse.urlencode(
        {"fields": "quota_usage,config", "access_token": tk}))
    d = (r.get("data") or [{}])[0]
    return d.get("quota_usage", 0), d.get("config", {}).get("quota_total", 100)


def problemas(item):
    p = []
    cap = item.get("caption", "")
    if not cap.strip():
        p.append("caption vacío")
    if PROHIBIDO.search(cap):
        p.append(f"palabra prohibida: {PROHIBIDO.search(cap).group(0)}")
    if TELEFONO.search(cap):
        p.append("parece tener un teléfono (el WhatsApp va solo en la placa)")
    if item.get("franja") not in FRANJAS:
        p.append(f"franja {item.get('franja')} no es 8, 12 ni 21")
    video = item.get("video", "")
    if not video.endswith(".mp4") or not os.path.isfile(os.path.join(RAIZ, video)):
        p.append(f"no encuentro el video {video} en el repo")
    return p


def publicar(tk, item):
    datos = {
        "media_type": "REELS",
        "video_url": RAW + urllib.parse.quote(item["video"]),
        "caption": item["caption"].strip(),
        "trial_params": json.dumps({"graduation_strategy": "MANUAL"}),
        "access_token": tk,
    }
    if item.get("portada_ms") is not None:
        datos["thumb_offset"] = str(int(item["portada_ms"]))
    cid = pedir("POST", f"{GRAPH}/{IG_USER_ID}/media", datos)["id"]
    for _ in range(60):
        st = pedir("GET", f"{GRAPH}/{cid}?" + urllib.parse.urlencode(
            {"fields": "status_code,status", "access_token": tk}))
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram rechazó el video: {st}")
        time.sleep(10)
    else:
        raise RuntimeError("Instagram tardó más de 10 min en procesar el video")
    mid = pedir("POST", f"{GRAPH}/{IG_USER_ID}/media_publish",
                {"creation_id": cid, "access_token": tk})["id"]
    info = pedir("GET", f"{GRAPH}/{mid}?" + urllib.parse.urlencode(
        {"fields": "permalink", "access_token": tk}))
    return mid, info.get("permalink", "")


def main():
    seco = "--seco" in sys.argv
    tk = os.environ.get("IG_TOKEN", "").strip()
    if not tk:
        sys.exit("FALTA IG_TOKEN en el entorno. No se publicó nada.")
    ahora = dt.datetime.now(TUC)
    cola = json.load(open(COLA, encoding="utf-8"))
    items = cola.get("items", [])
    print(f"Ahora (Tucumán): {ahora:%Y-%m-%d %H:%M} · {len(items)} ítems en la cola")

    hechos = ya_publicados(tk)
    usado, total = cupo(tk)
    print(f"Cupo de la API: {usado}/{total}")

    toca = []
    for it in items:
        nombre = it.get("id", "?")
        if it.get("publicado"):
            continue
        if not it.get("aprobado"):
            print(f"- {nombre}: sin aprobar, no sale")
            continue
        try:
            hora = dt.datetime.fromisoformat(it["fecha"]).replace(
                hour=int(it["franja"]), tzinfo=TUC)
        except Exception:
            print(f"- {nombre}: fecha/franja inválida, no sale")
            continue
        if hora > ahora + dt.timedelta(minutes=20):
            continue
        if ahora - hora > ATRASO_MAX:
            print(f"- {nombre}: VENCIDO ({hora:%d/%m %H} h), no sale solo")
            continue
        if normal(it.get("caption")) in hechos:
            print(f"- {nombre}: ya está en Instagram, lo salteo")
            it["publicado"] = {"nota": "ya estaba en Instagram"}
            continue
        malo = problemas(it)
        if malo:
            print(f"- {nombre}: FRENADO: {'; '.join(malo)}")
            continue
        toca.append((hora, it))

    toca.sort(key=lambda x: x[0])
    toca = toca[:MAX_POR_CORRIDA]
    if not toca:
        print("Nada para publicar en esta franja.")
    if usado + len(toca) > total:
        sys.exit("Sin cupo en la API. No se publicó nada.")

    salida = 0
    for hora, it in toca:
        if seco:
            print(f"+ {it['id']}: PUBLICARÍA como trial ({hora:%d/%m %H} h) {it['video']}")
            continue
        try:
            mid, link = publicar(tk, it)
            it["publicado"] = {"id": mid, "link": link, "cuando": dt.datetime.now(TUC).isoformat(timespec="minutes")}
            print(f"+ {it['id']}: PUBLICADO como trial {link}")
        except Exception as e:
            print(f"! {it['id']}: ERROR {e}")
            salida = 1

    if not seco:
        with open(COLA, "w", encoding="utf-8") as f:
            json.dump(cola, f, ensure_ascii=False, indent=2)
            f.write("\n")
    sys.exit(salida)


if __name__ == "__main__":
    main()
