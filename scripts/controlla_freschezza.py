#!/usr/bin/env python3
"""Controlla da quanto tempo non riesce un aggiornamento dei dati.

Serve contro il guasto silenzioso: quando lo scheduler di GitHub salta del
tutto un'esecuzione non c'e nessun fallimento da segnalare, quindi nessuna mail,
e la pagina resta ferma senza che nessuno se ne accorga.

    python3 scripts/controlla_freschezza.py              # riferisce e basta
    python3 scripts/controlla_freschezza.py --esci-male  # esce con errore se stantio

Il secondo modo serve in coda al workflow di sorveglianza: un'uscita con errore
fa fallire l'esecuzione, e un'esecuzione fallita la mail la manda.
"""

import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LATEST = os.path.join(ROOT, "docs", "latest.json")

# Stessa soglia dell'avviso in pagina. Fra il giro delle 15:15 e quello delle
# 06:45 passano 15 ore e mezza di silenzio del tutto normali: sotto le 18 ore
# non e successo niente.
# Il "or" copre anche la variabile presente ma vuota, che e come arriva da un
# input facoltativo di GitHub Actions lasciato in bianco.
ORE_LIMITE = float(os.environ.get("ORE_LIMITE") or 18)


def eta_ore():
    """Ore trascorse dall'ultimo aggiornamento riuscito, o None se illeggibile."""
    try:
        with open(LATEST, encoding="utf-8") as handle:
            generato = json.load(handle)["generato"]
        istante = datetime.strptime(generato, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    except (OSError, ValueError, KeyError) as errore:
        print("Dati illeggibili (%s): li tratto come stantii." % errore, file=sys.stderr)
        return None, None
    return (datetime.now(timezone.utc) - istante).total_seconds() / 3600, generato


def main():
    ore, generato = eta_ore()
    stantio = ore is None or ore > ORE_LIMITE

    if ore is None:
        print("Ultimo aggiornamento: non determinabile")
    else:
        print("Ultimo aggiornamento: %s (%.1f ore fa, soglia %.0f)"
              % (generato, ore, ORE_LIMITE))
    print("Esito: %s" % ("DATI STANTII" if stantio else "dati freschi"))

    # Passa il risultato al workflow, che decide se tentare la riparazione.
    percorso = os.environ.get("GITHUB_OUTPUT")
    if percorso:
        with open(percorso, "a", encoding="utf-8") as handle:
            handle.write("stantio=%s\n" % ("true" if stantio else "false"))

    if stantio and "--esci-male" in sys.argv:
        sys.exit(
            "I dati sono ancora fermi dopo il tentativo di aggiornamento: "
            "controlla se i feed della NASA rispondono."
        )


if __name__ == "__main__":
    main()
