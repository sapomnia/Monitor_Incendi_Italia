"""Scaricamento HTTP con qualche tentativo, condiviso da tutti gli script.

I server della NASA ogni tanto rispondono 502 o 503 per qualche secondo. Senza
riprovare, un singhiozzo di rete di dieci secondi fa saltare un sensore intero
per tutta la giornata, o nel caso peggiore l'aggiornamento completo.
"""

import sys
import time
import urllib.error
import urllib.request

TENTATIVI = 3
ATTESA_INIZIALE_S = 5
ATTESA_MASSIMA_S = 60

# Codici che vale la pena riprovare: sono disservizi passeggeri o richieste di
# rallentare. Un 404 o un 403 invece non cambiano riprovando.
CODICI_RIPROVABILI = frozenset({408, 425, 429, 500, 502, 503, 504})

INTESTAZIONI = {"User-Agent": "Monitor_Incendi_Italia/1.0 (+github.com/sapomnia)"}


def scarica(url, etichetta, tentativi=TENTATIVI, timeout=120):
    """Scarica un URL come testo, o restituisce None dopo aver esaurito i tentativi."""
    attesa = ATTESA_INIZIALE_S

    for tentativo in range(1, tentativi + 1):
        try:
            richiesta = urllib.request.Request(url, headers=INTESTAZIONI)
            with urllib.request.urlopen(richiesta, timeout=timeout) as risposta:
                return risposta.read().decode("utf-8", errors="replace")

        except urllib.error.HTTPError as errore:
            # HTTPError deriva da URLError: va intercettato per primo.
            riprovabile = errore.code in CODICI_RIPROVABILI
            motivo = "HTTP %d" % errore.code
            # Se il server dice esplicitamente quanto aspettare, gli diamo retta.
            richiesto = errore.headers.get("Retry-After") if errore.headers else None
            if richiesto:
                try:
                    attesa = max(attesa, min(ATTESA_MASSIMA_S, int(richiesto)))
                except (TypeError, ValueError):
                    pass

        except (urllib.error.URLError, TimeoutError, OSError) as errore:
            # Timeout, DNS che non risolve, connessione azzerata: tutta roba che
            # spesso si risolve da sola al tentativo dopo.
            riprovabile = True
            motivo = str(errore)

        else:
            continue

        ultimo = tentativo == tentativi
        if not riprovabile or ultimo:
            print(
                "  ERRORE su %s: %s%s"
                % (etichetta, motivo, "" if riprovabile else " (inutile riprovare)"),
                file=sys.stderr,
            )
            return None

        print(
            "  %s: %s, riprovo tra %d secondi (tentativo %d di %d)"
            % (etichetta, motivo, attesa, tentativo + 1, tentativi),
            file=sys.stderr,
        )
        time.sleep(attesa)
        attesa = min(ATTESA_MASSIMA_S, attesa * 2)

    return None
