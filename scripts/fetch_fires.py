#!/usr/bin/env python3
"""Raccoglie i rilevamenti di incendio NASA FIRMS sull'Italia nelle ultime 24 ore.

Scarica i feed satellitari, tiene la finestra delle ultime 24 ore in UTC,
ritaglia i punti sui confini regionali reali, raggruppa i rilevamenti vicini in
focolai e scrive i file che alimentano la pagina.

    python3 scripts/fetch_fires.py

Di default usa i feed pubblici FIRMS, che non richiedono chiave. Se la variabile
d'ambiente FIRMS_MAP_KEY e valorizzata usa invece l'API ufficiale per area, che
consente il ritaglio geografico a monte ed e la fonte da preferire se ce l'hai.

Solo libreria standard: nessuna dipendenza da installare.
"""

import csv
import glob
import io
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from rete import scarica

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOUNDARIES = os.path.join(ROOT, "docs", "regioni.json")
LATEST = os.path.join(ROOT, "docs", "latest.json")
HISTORY = os.path.join(ROOT, "docs", "storico.json")
ARCHIVE_DIR = os.path.join(ROOT, "data", "archive")
MASK = os.path.join(ROOT, "data", "hotspot_permanenti.json")
OVERRIDE = os.path.join(ROOT, "data", "hotspot_override.json")

# Bounding box dell'Italia, con un margine. Serve solo a scremare in fretta i
# feed europei: il ritaglio vero e fatto sui poligoni regionali.
BBOX = (6.5, 35.3, 18.7, 47.2)

FEED_BASE = "https://firms.modaps.eosdis.nasa.gov/data/active_fire"
API_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# I quattro sensori attivi. VIIRS ha risoluzione 375 m, MODIS 1 km: teniamo
# MODIS perche allunga la serie storica e vede passaggi in orari diversi.
SOURCES = [
    {
        "id": "VIIRS_SNPP",
        "etichetta": "VIIRS Suomi-NPP",
        "feed": FEED_BASE + "/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Europe_24h.csv",
        "api": "VIIRS_SNPP_NRT",
    },
    {
        "id": "VIIRS_NOAA20",
        "etichetta": "VIIRS NOAA-20",
        "feed": FEED_BASE + "/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Europe_24h.csv",
        "api": "VIIRS_NOAA20_NRT",
    },
    {
        "id": "VIIRS_NOAA21",
        "etichetta": "VIIRS NOAA-21",
        "feed": FEED_BASE + "/noaa-21-viirs-c2/csv/J2_VIIRS_C2_Europe_24h.csv",
        "api": "VIIRS_NOAA21_NRT",
    },
    {
        "id": "MODIS",
        "etichetta": "MODIS Terra/Aqua",
        "feed": FEED_BASE + "/modis-c6.1/csv/MODIS_C6_1_Europe_24h.csv",
        "api": "MODIS_NRT",
    },
]

# I rilevamenti a bassa confidenza sono in larga parte falsi positivi (superfici
# calde, riverberi). Restano nell'archivio ma non entrano nei conteggi pubblici,
# come da prassi nell'uso giornalistico dei dati FIRMS.
CONFIDENZE_PUBBLICATE = {"nominale", "alta"}

# Due rilevamenti entro questa distanza sono considerati lo stesso focolaio.
# Il pixel VIIRS e 375 m e MODIS 1 km: 1,5 km unisce i pixel adiacenti di uno
# stesso incendio e le doppie osservazioni di satelliti diversi.
DISTANZA_FOCOLAIO_M = 1500

# Raggio entro cui un rilevamento e attribuito a un punto caldo permanente
# (acciaieria, raffineria, vulcano). Vedi scripts/build_hotspot_mask.py.
RAGGIO_PUNTO_CALDO_M = 1000

GIORNI_STORICO = 60


# --------------------------------------------------------------------------
# Geometria
# --------------------------------------------------------------------------

def punto_in_anello(x, y, ring):
    """Ray casting: True se il punto e dentro l'anello."""
    dentro = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                dentro = not dentro
        j = i
    return dentro


def bbox_anello(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


class Regioni:
    """Confini regionali pronti per il test punto-dentro-poligono."""

    def __init__(self, percorso):
        with open(percorso, encoding="utf-8") as handle:
            collection = json.load(handle)

        self.regioni = []
        for feature in collection["features"]:
            poligoni = []
            for rings in feature["geometry"]["coordinates"]:
                esterno = rings[0]
                buchi = rings[1:]
                poligoni.append((bbox_anello(esterno), esterno, buchi))
            xs = [b[0] for b, _, _ in poligoni] + [b[2] for b, _, _ in poligoni]
            ys = [b[1] for b, _, _ in poligoni] + [b[3] for b, _, _ in poligoni]
            self.regioni.append(
                {
                    "nome": feature["properties"]["nome"],
                    "istat": feature["properties"]["istat"],
                    "bbox": (min(xs), min(ys), max(xs), max(ys)),
                    "poligoni": poligoni,
                }
            )

    def localizza(self, lon, lat):
        """Nome della regione che contiene il punto, o None se fuori dall'Italia."""
        for regione in self.regioni:
            minx, miny, maxx, maxy = regione["bbox"]
            if not (minx <= lon <= maxx and miny <= lat <= maxy):
                continue
            for bbox, esterno, buchi in regione["poligoni"]:
                minx, miny, maxx, maxy = bbox
                if not (minx <= lon <= maxx and miny <= lat <= maxy):
                    continue
                if not punto_in_anello(lon, lat, esterno):
                    continue
                # Un punto dentro un buco e fuori dall'Italia: San Marino,
                # Citta del Vaticano, Campione d'Italia.
                if any(punto_in_anello(lon, lat, buco) for buco in buchi):
                    continue
                return regione["nome"], regione["istat"]
        return None


def distanza_m(lat1, lon1, lat2, lon2):
    """Distanza in metri, formula dell'emisenoverso."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------
# Scarico e normalizzazione
# --------------------------------------------------------------------------

def normalizza_confidenza(valore):
    """VIIRS usa l/n/h, MODIS una percentuale 0-100."""
    valore = (valore or "").strip().lower()
    if valore in ("l", "low"):
        return "bassa"
    if valore in ("n", "nominal"):
        return "nominale"
    if valore in ("h", "high"):
        return "alta"
    try:
        percentuale = float(valore)
    except ValueError:
        return "nominale"
    if percentuale < 30:
        return "bassa"
    if percentuale < 80:
        return "nominale"
    return "alta"


def leggi_righe(testo, sorgente):
    """Converte il CSV FIRMS in dizionari con gli stessi campi per ogni sensore."""
    righe = []
    for record in csv.DictReader(io.StringIO(testo)):
        try:
            lat = float(record["latitude"])
            lon = float(record["longitude"])
        except (KeyError, TypeError, ValueError):
            continue

        # Scrematura rapida sul bounding box prima del test sui poligoni.
        if not (BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]):
            continue

        ora = str(record.get("acq_time", "")).strip().zfill(4)
        try:
            istante = datetime.strptime(
                record["acq_date"].strip() + ora, "%Y-%m-%d%H%M"
            ).replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue

        try:
            frp = float(record.get("frp") or 0.0)
        except ValueError:
            frp = 0.0

        righe.append(
            {
                "lat": lat,
                "lon": lon,
                "istante": istante,
                "frp": frp,
                "confidenza": normalizza_confidenza(record.get("confidence")),
                "giorno_notte": (record.get("daynight") or "").strip().upper(),
                "sorgente": sorgente["id"],
            }
        )
    return righe


def carica_punti_caldi():
    """Legge la maschera dei punti caldi permanenti e le correzioni manuali.

    Restituisce due liste di coordinate: quelle da escludere e quelle da
    salvare comunque. La seconda serve quando un incendio vero divampa accanto
    a un impianto industriale gia mascherato.
    """
    escludi = []
    if os.path.exists(MASK):
        try:
            with open(MASK, encoding="utf-8") as handle:
                dati = json.load(handle)
            escludi = [(p["lat"], p["lon"], p["stato"]) for p in dati.get("permanenti", [])]
        except (ValueError, OSError, KeyError):
            print("  ATTENZIONE: maschera punti caldi illeggibile, la ignoro", file=sys.stderr)

    salva = []
    if os.path.exists(OVERRIDE):
        try:
            with open(OVERRIDE, encoding="utf-8") as handle:
                dati = json.load(handle)
            escludi += [(v["lat"], v["lon"], "manuale") for v in dati.get("sempre_escludi", [])]
            salva = [(v["lat"], v["lon"]) for v in dati.get("sempre_includi", [])]
        except (ValueError, OSError, KeyError):
            print("  ATTENZIONE: file di override illeggibile, lo ignoro", file=sys.stderr)

    return escludi, salva


def vicino_a(punto, coordinate, raggio_m):
    for voce in coordinate:
        if distanza_m(punto["lat"], punto["lon"], voce[0], voce[1]) <= raggio_m:
            return voce
    return None


def raccogli(sorgente, map_key):
    """Scarica una sorgente, dall'API se c'e la chiave, altrimenti dal feed pubblico."""
    if map_key:
        area = "%s,%s,%s,%s" % (BBOX[0], BBOX[1], BBOX[2], BBOX[3])
        # day_range=2: la finestra di 24 ore a cavallo della mezzanotte UTC
        # tocca due giorni di calendario, il filtro fine e fatto dopo.
        url = "%s/%s/%s/%s/2" % (API_BASE, map_key, sorgente["api"], area)
        etichetta = sorgente["etichetta"] + " (API)"
    else:
        url = sorgente["feed"]
        etichetta = sorgente["etichetta"] + " (feed pubblico)"

    testo = scarica(url, etichetta)
    if testo is None:
        return None
    # L'API risponde 200 anche con un messaggio d'errore in chiaro.
    if not testo.lstrip().lower().startswith("latitude"):
        print("  ERRORE su %s: risposta inattesa: %s" % (etichetta, testo[:120].strip()),
              file=sys.stderr)
        return None

    righe = leggi_righe(testo, sorgente)
    print("  %-34s %4d nel riquadro Italia" % (etichetta, len(righe)))
    return righe


# --------------------------------------------------------------------------
# Raggruppamento in focolai
# --------------------------------------------------------------------------

def raggruppa(punti):
    """Unisce i rilevamenti vicini: piu pixel accesi sono lo stesso incendio.

    Union-find con indice a griglia, per non confrontare tutte le coppie.
    """
    padre = list(range(len(punti)))

    def trova(i):
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i

    def unisci(a, b):
        ra, rb = trova(a), trova(b)
        if ra != rb:
            padre[rb] = ra

    # Celle da circa 2 km: due punti a meno di 1,5 km stanno nella stessa cella
    # o in una delle otto adiacenti.
    passo = 0.02
    griglia = defaultdict(list)
    for indice, punto in enumerate(punti):
        griglia[(int(punto["lon"] / passo), int(punto["lat"] / passo))].append(indice)

    for indice, punto in enumerate(punti):
        cx, cy = int(punto["lon"] / passo), int(punto["lat"] / passo)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for altro in griglia.get((cx + dx, cy + dy), ()):
                    if altro <= indice:
                        continue
                    if distanza_m(
                        punto["lat"], punto["lon"],
                        punti[altro]["lat"], punti[altro]["lon"],
                    ) <= DISTANZA_FOCOLAIO_M:
                        unisci(indice, altro)

    gruppi = defaultdict(list)
    for indice in range(len(punti)):
        gruppi[trova(indice)].append(punti[indice])

    focolai = []
    for membri in gruppi.values():
        frp_totale = sum(m["frp"] for m in membri)
        # Baricentro pesato sull'intensita: il centro cade dove il fuoco e piu forte.
        peso = frp_totale if frp_totale > 0 else float(len(membri))
        if frp_totale > 0:
            lat = sum(m["lat"] * m["frp"] for m in membri) / peso
            lon = sum(m["lon"] * m["frp"] for m in membri) / peso
        else:
            lat = sum(m["lat"] for m in membri) / len(membri)
            lon = sum(m["lon"] for m in membri) / len(membri)

        ultimo = max(membri, key=lambda m: m["istante"])
        confidenze = {m["confidenza"] for m in membri}
        focolai.append(
            {
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "frp": round(frp_totale, 1),
                "rilevamenti": len(membri),
                "regione": ultimo["regione"],
                "istante": ultimo["istante"].strftime("%Y-%m-%dT%H:%MZ"),
                "confidenza": "alta" if "alta" in confidenze else "nominale",
                # Un fuoco ancora acceso di notte non e una bruciatura agricola
                # sorvegliata: e un incendio che sta continuando da solo.
                "notte": any(m["giorno_notte"] == "N" for m in membri),
                "sorgenti": sorted({m["sorgente"] for m in membri}),
            }
        )

    focolai.sort(key=lambda f: f["frp"], reverse=True)
    return focolai


# --------------------------------------------------------------------------
# Storico
# --------------------------------------------------------------------------

def unisci_intervalli(intervalli):
    """Fonde gli intervalli temporali che si sovrappongono."""
    if not intervalli:
        return []
    ordinati = sorted(intervalli)
    uniti = [list(ordinati[0])]
    for inizio, fine in ordinati[1:]:
        if inizio <= uniti[-1][1]:
            uniti[-1][1] = max(uniti[-1][1], fine)
        else:
            uniti.append([inizio, fine])
    return uniti


def ricostruisci_storico():
    """Ricalcola la serie giornaliera dall'archivio, per giorno di calendario.

    Non dipende piu da quando gira il cron: il conteggio del 30 luglio resta il
    conteggio del 30 luglio, chiunque lo calcoli e a qualunque ora. Un giro in
    ritardo o saltato non lascia buchi, perche il giro successivo ricostruisce
    tutto da capo dai rilevamenti archiviati.

    I giorni sono in ora UTC. Lo scarto con il calendario italiano e di una o
    due ore e cade in piena notte, quando i satelliti non sorvolano l'Italia:
    non sposta i conteggi.
    """
    file_archivio = sorted(glob.glob(os.path.join(ARCHIVE_DIR, "*.json")))
    if not file_archivio:
        return []

    # Le finestre dei vari giri si sovrappongono: teniamo ogni rilevamento una
    # volta sola. Coordinate, istante e sensore identificano l'osservazione.
    osservazioni = {}
    coperture = []

    for percorso in file_archivio:
        try:
            with open(percorso, encoding="utf-8") as handle:
                dati = json.load(handle)
        except (ValueError, OSError):
            print("  ATTENZIONE: archivio illeggibile, lo salto: %s"
                  % os.path.basename(percorso), file=sys.stderr)
            continue

        finestra = dati.get("finestra") or {}
        if finestra.get("inizio") and finestra.get("fine"):
            try:
                coperture.append((
                    datetime.strptime(finestra["inizio"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc),
                    datetime.strptime(finestra["fine"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc),
                ))
            except ValueError:
                pass

        for voce in dati.get("rilevamenti", []):
            # Stesse regole del conteggio pubblico: niente bassa attendibilita,
            # niente acciaierie e vulcani.
            if voce.get("confidenza") not in CONFIDENZE_PUBBLICATE:
                continue
            if voce.get("punto_caldo_permanente"):
                continue
            chiave = (voce["lat"], voce["lon"], voce["istante"], voce["sorgente"])
            osservazioni[chiave] = voce

    if not osservazioni:
        return []

    coperture = unisci_intervalli(coperture)

    def giorno_completo(giorno):
        """Vero se l'archivio copre le 24 ore piene di quel giorno."""
        inizio = datetime.strptime(giorno, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        fine = inizio + timedelta(days=1)
        return any(a <= inizio and fine <= b for a, b in coperture)

    per_giorno = defaultdict(list)
    for voce in osservazioni.values():
        try:
            istante = datetime.strptime(voce["istante"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        per_giorno[voce["istante"][:10]].append({
            "lat": voce["lat"],
            "lon": voce["lon"],
            "frp": voce.get("frp", 0.0),
            "istante": istante,
            "confidenza": voce["confidenza"],
            "giorno_notte": voce.get("giorno_notte", ""),
            "sorgente": voce["sorgente"],
            "regione": voce.get("regione", ""),
        })

    storico = []
    for giorno in sorted(per_giorno):
        # Il giorno in corso, e quelli non ancora coperti per intero, restano
        # fuori: un conteggio parziale in fondo al grafico sembrerebbe un crollo.
        if not giorno_completo(giorno):
            continue
        punti = per_giorno[giorno]
        storico.append({
            "giorno": giorno,
            "rilevamenti": len(punti),
            "focolai": len(raggruppa(punti)),
            "frp": round(sum(p["frp"] for p in punti), 1),
        })

    storico = storico[-GIORNI_STORICO:]
    with open(HISTORY, "w", encoding="utf-8") as handle:
        json.dump({"giorni": storico}, handle, ensure_ascii=False, separators=(",", ":"))
    return storico


# --------------------------------------------------------------------------

def main():
    adesso = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    inizio_finestra = adesso - timedelta(hours=24)
    map_key = os.environ.get("FIRMS_MAP_KEY", "").strip()

    print("Finestra: %s -> %s UTC" % (
        inizio_finestra.strftime("%Y-%m-%d %H:%M"), adesso.strftime("%Y-%m-%d %H:%M")))
    print("Fonte:    %s" % ("API FIRMS con MAP_KEY" if map_key else "feed pubblici FIRMS"))

    if not os.path.exists(BOUNDARIES):
        sys.exit("Confini mancanti: esegui prima scripts/build_boundaries.py")
    regioni = Regioni(BOUNDARIES)

    print("Scarico i sensori ...")
    grezzi = []
    sorgenti_ok = []
    for sorgente in SOURCES:
        righe = raccogli(sorgente, map_key)
        if righe is None:
            continue
        sorgenti_ok.append(sorgente["id"])
        grezzi.extend(righe)

    if not sorgenti_ok:
        sys.exit("Nessuna sorgente raggiungibile: non aggiorno i dati.")

    # Finestra temporale.
    in_finestra = [r for r in grezzi if inizio_finestra <= r["istante"] <= adesso]

    # Ritaglio sui confini reali: il riquadro comprende anche Austria, Slovenia,
    # Croazia, Corsica e Tunisia.
    italiani = []
    for punto in in_finestra:
        posizione = regioni.localizza(punto["lon"], punto["lat"])
        if posizione is None:
            continue
        punto["regione"], punto["istat"] = posizione
        italiani.append(punto)

    attendibili = [p for p in italiani if p["confidenza"] in CONFIDENZE_PUBBLICATE]
    scartati_confidenza = len(italiani) - len(attendibili)

    # Punti caldi permanenti: impianti industriali e vulcani, che i satelliti
    # rilevano tutti i giorni e che non sono incendi.
    escludi, salva = carica_punti_caldi()
    pubblicabili = []
    esclusi_permanenti = []
    for punto in attendibili:
        colpito = vicino_a(punto, escludi, RAGGIO_PUNTO_CALDO_M)
        if colpito is not None and vicino_a(punto, salva, RAGGIO_PUNTO_CALDO_M) is None:
            punto["motivo_esclusione"] = colpito[2]
            esclusi_permanenti.append(punto)
            continue
        pubblicabili.append(punto)

    print("Rilevamenti: %d scaricati -> %d in finestra -> %d in Italia -> %d attendibili -> %d pubblicabili"
          % (len(grezzi), len(in_finestra), len(italiani), len(attendibili), len(pubblicabili)))
    if not escludi:
        print("  ATTENZIONE: maschera dei punti caldi permanenti assente.")
        print("  Esegui scripts/build_hotspot_mask.py, altrimenti acciaierie e")
        print("  vulcani verranno conteggiati come incendi.")

    focolai = raggruppa(pubblicabili)
    print("Focolai stimati: %d" % len(focolai))

    per_regione = defaultdict(lambda: {"rilevamenti": 0, "focolai": 0, "frp": 0.0, "notturni": 0})
    for punto in pubblicabili:
        voce = per_regione[punto["regione"]]
        voce["rilevamenti"] += 1
        voce["frp"] += punto["frp"]
    for focolaio in focolai:
        voce = per_regione[focolaio["regione"]]
        voce["focolai"] += 1
        if focolaio["notte"]:
            voce["notturni"] += 1

    # Ordiniamo per intensita e non per numero di focolai: dieci bruciature di
    # stoppie in pianura non sono un'emergenza, due incendi che di notte
    # continuano a bruciare sulle montagne calabresi si.
    classifica = sorted(
        (
            {
                "regione": nome,
                "rilevamenti": voce["rilevamenti"],
                "focolai": voce["focolai"],
                "notturni": voce["notturni"],
                "frp": round(voce["frp"], 1),
            }
            for nome, voce in per_regione.items()
        ),
        key=lambda v: (v["frp"], v["focolai"]),
        reverse=True,
    )

    per_sorgente = defaultdict(int)
    for punto in pubblicabili:
        per_sorgente[punto["sorgente"]] += 1

    ultimo = max((p["istante"] for p in pubblicabili), default=None)

    risultato = {
        "generato": adesso.strftime("%Y-%m-%dT%H:%MZ"),
        "finestra": {
            "inizio": inizio_finestra.strftime("%Y-%m-%dT%H:%MZ"),
            "fine": adesso.strftime("%Y-%m-%dT%H:%MZ"),
            "ultimo_rilevamento": ultimo.strftime("%Y-%m-%dT%H:%MZ") if ultimo else None,
        },
        "totali": {
            "rilevamenti": len(pubblicabili),
            "focolai": len(focolai),
            "frp": round(sum(p["frp"] for p in pubblicabili), 1),
            "esclusi_bassa_confidenza": scartati_confidenza,
            "esclusi_punti_caldi": len(esclusi_permanenti),
        },
        # Elenco in chiaro di cosa e stato tolto dai conteggi, per poterlo
        # controllare invece di doversi fidare.
        "punti_caldi_esclusi": [
            {
                "lat": round(p["lat"], 4),
                "lon": round(p["lon"], 4),
                "regione": p["regione"],
                "frp": p["frp"],
                "motivo": p["motivo_esclusione"],
            }
            for p in sorted(esclusi_permanenti, key=lambda p: -p["frp"])
        ],
        "per_regione": classifica,
        "per_sorgente": [
            {"id": s["id"], "etichetta": s["etichetta"], "rilevamenti": per_sorgente.get(s["id"], 0)}
            for s in SOURCES
            if s["id"] in sorgenti_ok
        ],
        "sorgenti_non_raggiunte": [
            s["etichetta"] for s in SOURCES if s["id"] not in sorgenti_ok
        ],
        "focolai": focolai,
        "fonte": "NASA FIRMS (VIIRS 375 m, MODIS 1 km) - dati near real-time",
    }

    with open(LATEST, "w", encoding="utf-8") as handle:
        json.dump(risultato, handle, ensure_ascii=False, separators=(",", ":"))

    # Archivio: tutti i rilevamenti, bassa confidenza compresa, per le analisi future.
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    giorno = adesso.strftime("%Y-%m-%d")
    archivio = os.path.join(ARCHIVE_DIR, giorno + ".json")
    with open(archivio, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "generato": risultato["generato"],
                "finestra": risultato["finestra"],
                "rilevamenti": [
                    {
                        "lat": round(p["lat"], 5),
                        "lon": round(p["lon"], 5),
                        "istante": p["istante"].strftime("%Y-%m-%dT%H:%MZ"),
                        "frp": p["frp"],
                        "confidenza": p["confidenza"],
                        "giorno_notte": p["giorno_notte"],
                        "sorgente": p["sorgente"],
                        "regione": p["regione"],
                        "istat": p["istat"],
                        "punto_caldo_permanente": p.get("motivo_esclusione"),
                    }
                    for p in sorted(italiani, key=lambda p: p["istante"])
                ],
            },
            handle,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    # La serie giornaliera viene ricostruita da zero dall'archivio a ogni giro:
    # e indipendente dall'orario di esecuzione, quindi non risente dei ritardi
    # ne delle esecuzioni saltate dallo scheduler di GitHub.
    storico = ricostruisci_storico()

    print()
    print("Scritto: %s (%.0f KB)" % (os.path.relpath(LATEST, ROOT), os.path.getsize(LATEST) / 1024))
    print("Scritto: %s" % os.path.relpath(archivio, ROOT))
    if storico:
        print("Storico: %d giorni completi (%s -> %s)"
              % (len(storico), storico[0]["giorno"], storico[-1]["giorno"]))
    else:
        print("Storico: nessun giorno ancora coperto per intero dall'archivio")
    if esclusi_permanenti:
        print()
        print("Esclusi %d rilevamenti su punti caldi permanenti:" % len(esclusi_permanenti))
        for punto in sorted(esclusi_permanenti, key=lambda p: -p["frp"])[:8]:
            print("  %8.4f, %8.4f  %-24s FRP %6.1f  (%s)" % (
                punto["lat"], punto["lon"], punto["regione"], punto["frp"],
                punto["motivo_esclusione"]))

    if classifica:
        print()
        print("Regioni per intensita del fuoco:")
        for voce in classifica[:5]:
            print("  %-34s FRP %7.1f  %3d focolai (%d notturni)" % (
                voce["regione"], voce["frp"], voce["focolai"], voce["notturni"]))


if __name__ == "__main__":
    main()
