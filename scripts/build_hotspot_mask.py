#!/usr/bin/env python3
"""Individua i punti caldi permanenti: acciaierie, raffinerie, vulcani, torce.

I satelliti FIRMS non distinguono un incendio boschivo da un altoforno: senza
questo filtro il bresciano e la Val Padana risultano fra le aree piu "colpite"
d'Italia, e Stromboli brucia tutto l'anno.

Il criterio e comportamentale, non una lista scritta a mano: un incendio brucia
in un posto per qualche giorno e non torna piu, un impianto industriale si
accende sempre nelle stesse coordinate. Lo script scarica i feed FIRMS a 7
giorni, accumula nel tempo i giorni in cui ogni punto si accende e classifica:

  confermato  visto in >= 4 giorni distinti su un arco di almeno 14 giorni:
              e una sorgente stabile, esclusa con sicurezza
  provvisorio visto in >= 4 giorni ma su un arco piu breve, perche l'archivio
              e ancora giovane: escluso, ma segnalato per la revisione

Piu a lungo gira il progetto, piu la maschera diventa affidabile: i provvisori
o vengono confermati o scadono da soli.

    python3 scripts/build_hotspot_mask.py

Da eseguire una volta a settimana. Solo libreria standard.
"""

import csv
import io
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASK = os.path.join(ROOT, "data", "hotspot_permanenti.json")

BBOX = (6.5, 35.3, 18.7, 47.2)
FEED_BASE = "https://firms.modaps.eosdis.nasa.gov/data/active_fire"

FEEDS_7D = [
    ("VIIRS Suomi-NPP", FEED_BASE + "/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Europe_7d.csv"),
    ("VIIRS NOAA-20", FEED_BASE + "/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Europe_7d.csv"),
    ("VIIRS NOAA-21", FEED_BASE + "/noaa-21-viirs-c2/csv/J2_VIIRS_C2_Europe_7d.csv"),
    ("MODIS Terra/Aqua", FEED_BASE + "/modis-c6.1/csv/MODIS_C6_1_Europe_7d.csv"),
]

# Celle da circa 800 m: la dimensione di un impianto industriale visto da VIIRS.
CELLA = 0.0075
# Soglie di classificazione.
GIORNI_MINIMI = 4
ARCO_CONFERMA_GIORNI = 14
# Un punto che non si accende da tanto tempo smette di essere considerato
# permanente: gli impianti chiudono, i vulcani si assopiscono.
SCADENZA_GIORNI = 120


def scarica(url, etichetta):
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "IncendiFanPage/1.0"})
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as errore:
        print("  ERRORE su %s: %s" % (etichetta, errore), file=sys.stderr)
        return None


def chiave_cella(lat, lon):
    return "%d_%d" % (round(lat / CELLA), round(lon / CELLA))


def carica_maschera():
    if not os.path.exists(MASK):
        return {}
    try:
        with open(MASK, encoding="utf-8") as handle:
            return {v["cella"]: v for v in json.load(handle).get("punti", [])}
    except (ValueError, OSError, KeyError):
        print("ATTENZIONE: maschera illeggibile, la ricostruisco", file=sys.stderr)
        return {}


def main():
    oggi = datetime.now(timezone.utc).date()
    punti = carica_maschera()
    print("Maschera esistente: %d punti censiti" % len(punti))

    print("Scarico i feed a 7 giorni ...")
    osservazioni = defaultdict(lambda: {"giorni": set(), "lat": 0.0, "lon": 0.0, "n": 0})
    feed_ok = 0

    for etichetta, url in FEEDS_7D:
        testo = scarica(url, etichetta)
        if testo is None or not testo.lstrip().lower().startswith("latitude"):
            continue
        feed_ok += 1
        conteggio = 0
        for record in csv.DictReader(io.StringIO(testo)):
            try:
                lat = float(record["latitude"])
                lon = float(record["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]):
                continue
            giorno = record.get("acq_date", "").strip()
            if not giorno:
                continue
            voce = osservazioni[chiave_cella(lat, lon)]
            voce["giorni"].add(giorno)
            # Media progressiva delle coordinate: il centro della sorgente.
            voce["n"] += 1
            voce["lat"] += (lat - voce["lat"]) / voce["n"]
            voce["lon"] += (lon - voce["lon"]) / voce["n"]
            conteggio += 1
        print("  %-24s %5d rilevamenti" % (etichetta, conteggio))

    if feed_ok == 0:
        sys.exit("Nessun feed raggiungibile: lascio la maschera invariata.")

    # Fonde le nuove osservazioni nello storico cumulativo.
    for cella, voce in osservazioni.items():
        esistente = punti.get(cella)
        if esistente is None:
            punti[cella] = {
                "cella": cella,
                "lat": round(voce["lat"], 4),
                "lon": round(voce["lon"], 4),
                "giorni": sorted(voce["giorni"]),
            }
        else:
            esistente["giorni"] = sorted(set(esistente["giorni"]) | voce["giorni"])
            esistente["lat"] = round((esistente["lat"] + voce["lat"]) / 2, 4)
            esistente["lon"] = round((esistente["lon"] + voce["lon"]) / 2, 4)

    # Classificazione e potatura.
    permanenti = []
    scaduti = 0
    for voce in punti.values():
        giorni = voce["giorni"]
        if len(giorni) < GIORNI_MINIMI:
            continue
        primo = date.fromisoformat(giorni[0])
        ultimo = date.fromisoformat(giorni[-1])
        if (oggi - ultimo).days > SCADENZA_GIORNI:
            scaduti += 1
            continue
        arco = (ultimo - primo).days
        permanenti.append(
            {
                "cella": voce["cella"],
                "lat": voce["lat"],
                "lon": voce["lon"],
                "giorni_accesi": len(giorni),
                "arco_giorni": arco,
                "ultimo_giorno": giorni[-1],
                "stato": "confermato" if arco >= ARCO_CONFERMA_GIORNI else "provvisorio",
            }
        )

    # Nello storico teniamo solo i punti che possono ancora diventare permanenti,
    # altrimenti il file cresce all'infinito con gli incendi di un giorno solo.
    limite = (oggi - timedelta(days=SCADENZA_GIORNI)).isoformat()
    conservati = {
        cella: voce
        for cella, voce in punti.items()
        if len(voce["giorni"]) >= 2 and voce["giorni"][-1] >= limite
    }

    permanenti.sort(key=lambda p: (-p["giorni_accesi"], p["lat"]))
    confermati = [p for p in permanenti if p["stato"] == "confermato"]

    os.makedirs(os.path.dirname(MASK), exist_ok=True)
    with open(MASK, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "aggiornato": oggi.isoformat(),
                "criterio": (
                    "accesa in almeno %d giorni distinti; confermata se su un arco "
                    "di almeno %d giorni" % (GIORNI_MINIMI, ARCO_CONFERMA_GIORNI)
                ),
                "permanenti": permanenti,
                "punti": sorted(conservati.values(), key=lambda v: v["cella"]),
            },
            handle,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    print()
    print("Punti caldi permanenti: %d (%d confermati, %d provvisori)"
          % (len(permanenti), len(confermati), len(permanenti) - len(confermati)))
    if scaduti:
        print("Scaduti e rimossi: %d" % scaduti)
    print("Storico conservato: %d celle" % len(conservati))
    print("Scritto: %s (%.0f KB)" % (os.path.relpath(MASK, ROOT), os.path.getsize(MASK) / 1024))
    if permanenti:
        print()
        print("I piu persistenti (da controllare a occhio su una mappa):")
        for voce in permanenti[:15]:
            print("  %8.4f, %8.4f  %2d giorni su %3d  %s"
                  % (voce["lat"], voce["lon"], voce["giorni_accesi"],
                     voce["arco_giorni"], voce["stato"]))


if __name__ == "__main__":
    main()
