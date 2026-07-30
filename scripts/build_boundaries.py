#!/usr/bin/env python3
"""Genera i confini regionali italiani semplificati per la mappa e per il ritaglio dei punti.

Scarica il GeoJSON delle regioni (fonte: openpolis/geojson-italy, derivato da ISTAT),
lo semplifica con Douglas-Peucker e lo salva a un peso adatto al web.

Va eseguito una tantum (o quando cambiano i confini amministrativi):

    python3 scripts/build_boundaries.py

Solo libreria standard: nessuna dipendenza da installare.
"""

import json
import math
import os
import sys
import urllib.request

SOURCE_URL = (
    "https://raw.githubusercontent.com/openpolis/geojson-italy/master/"
    "geojson/limits_IT_regions.geojson"
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(ROOT, "docs", "regioni.json")

# Tolleranza Douglas-Peucker in gradi. 0.002 gradi ~ 200 m: sotto la dimensione
# del pixel VIIRS (375 m), quindi invisibile sia sulla mappa sia nel ritaglio.
TOLERANCE = 0.002
# Cifre decimali tenute: 4 ~ 11 m di precisione, piu che sufficienti.
PRECISION = 4
# Anelli piu piccoli di questa area (in gradi quadrati) vengono scartati: sono
# scogli e isolotti invisibili alla scala nazionale. ~0.3 km quadrati.
MIN_RING_AREA = 2.5e-5


def perpendicular_distance(point, start, end):
    """Distanza del punto dal segmento start-end, nel piano dei gradi."""
    (px, py), (sx, sy), (ex, ey) = point, start, end
    dx, dy = ex - sx, ey - sy
    if dx == 0 and dy == 0:
        return math.hypot(px - sx, py - sy)
    # Proiezione del punto sul segmento, vincolata agli estremi.
    t = ((px - sx) * dx + (py - sy) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (sx + t * dx), py - (sy + t * dy))


def douglas_peucker(points, tolerance):
    """Semplifica una polilinea mantenendo i vertici piu significativi.

    Implementazione iterativa: le coste italiane hanno anelli da decine di
    migliaia di punti e la versione ricorsiva sfonderebbe lo stack.
    """
    if len(points) < 3:
        return list(points)

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]

    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        max_dist, index = 0.0, first
        for i in range(first + 1, last):
            dist = perpendicular_distance(points[i], points[first], points[last])
            if dist > max_dist:
                max_dist, index = dist, i
        if max_dist > tolerance:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))

    return [p for p, k in zip(points, keep) if k]


def ring_area(ring):
    """Area con la formula di Gauss, in gradi quadrati (serve solo a confrontare)."""
    total = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def simplify_ring(ring):
    """Semplifica un anello chiuso, o restituisce None se degenera o e trascurabile."""
    if ring_area(ring) < MIN_RING_AREA:
        return None

    simplified = douglas_peucker(ring, TOLERANCE)
    # Un poligono valido ha almeno 3 vertici distinti piu la chiusura.
    if len(simplified) < 4:
        simplified = ring[:]
    # La semplificazione puo spostare l'ultimo vertice: richiudiamo l'anello.
    if simplified[0] != simplified[-1]:
        simplified.append(simplified[0])

    rounded = [[round(x, PRECISION), round(y, PRECISION)] for x, y in simplified]
    # Elimina vertici consecutivi diventati identici dopo l'arrotondamento.
    deduped = [rounded[0]]
    for point in rounded[1:]:
        if point != deduped[-1]:
            deduped.append(point)
    if len(deduped) < 4:
        return None
    if deduped[0] != deduped[-1]:
        deduped.append(deduped[0])
    return deduped


def iter_polygons(geometry):
    """Normalizza Polygon e MultiPolygon in una lista di poligoni.

    Ogni poligono e una lista di anelli: il primo e il contorno esterno, gli
    altri sono buchi (in Italia: San Marino dentro l'Emilia-Romagna, il
    Vaticano dentro il Lazio, i comuni di Campione d'Italia e simili).
    """
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"]
    raise ValueError("Geometria non gestita: " + geometry["type"])


def main():
    print("Scarico i confini da openpolis/geojson-italy ...")
    with urllib.request.urlopen(SOURCE_URL, timeout=120) as response:
        source = json.load(response)

    features = []
    stats = {"anelli_in": 0, "anelli_out": 0, "vertici_in": 0, "vertici_out": 0}

    for feature in source["features"]:
        name = feature["properties"]["reg_name"]
        istat = feature["properties"]["reg_istat_code"]

        polygons = []
        for rings in iter_polygons(feature["geometry"]):
            simplified_rings = []
            for position, ring in enumerate(rings):
                stats["anelli_in"] += 1
                stats["vertici_in"] += len(ring)
                simplified = simplify_ring(ring)
                if simplified is None:
                    # Se sparisce il contorno esterno salta tutto il poligono,
                    # se sparisce un buco pazienza: era piu piccolo di un pixel.
                    if position == 0:
                        simplified_rings = []
                        break
                    continue
                stats["anelli_out"] += 1
                stats["vertici_out"] += len(simplified)
                simplified_rings.append(simplified)
            if simplified_rings:
                polygons.append(simplified_rings)

        if not polygons:
            print("  ATTENZIONE: nessun poligono per " + name, file=sys.stderr)
            continue

        features.append(
            {
                "type": "Feature",
                "properties": {"nome": name, "istat": istat},
                "geometry": {"type": "MultiPolygon", "coordinates": polygons},
            }
        )
        print("  %-40s %2d poligoni" % (name, len(polygons)))

    if len(features) != 20:
        print(
            "ATTENZIONE: attese 20 regioni, trovate %d" % len(features),
            file=sys.stderr,
        )

    # Bounding box complessivo, usato dalla pagina per impostare la proiezione.
    xs = [x for f in features for poly in f["geometry"]["coordinates"] for ring in poly for x, _ in ring]
    ys = [y for f in features for poly in f["geometry"]["coordinates"] for ring in poly for _, y in ring]
    bbox = [min(xs), min(ys), max(xs), max(ys)]

    collection = {
        "type": "FeatureCollection",
        "bbox": [round(v, PRECISION) for v in bbox],
        "properties": {
            "fonte": "openpolis/geojson-italy (confini amministrativi ISTAT)",
            "licenza": "CC-BY 4.0",
            "semplificazione": "Douglas-Peucker, tolleranza %s gradi" % TOLERANCE,
        },
        "features": features,
    }

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(collection, handle, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(OUTPUT) / 1024
    print()
    print("Anelli:   %d -> %d" % (stats["anelli_in"], stats["anelli_out"]))
    print("Vertici:  %d -> %d (%.1f%%)" % (
        stats["vertici_in"],
        stats["vertici_out"],
        100.0 * stats["vertici_out"] / max(1, stats["vertici_in"]),
    ))
    print("Bbox:     %.3f, %.3f, %.3f, %.3f" % tuple(bbox))
    print("Scritto:  %s (%.0f KB)" % (os.path.relpath(OUTPUT, ROOT), size_kb))


if __name__ == "__main__":
    main()
