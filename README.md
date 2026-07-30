# Gli incendi in Italia nelle ultime 24 ore

Pagina aggiornata automaticamente ogni giorno con i focolai rilevati dai
satelliti NASA sul territorio italiano. Pensata per essere pubblicata su
Fanpage tramite iframe.

Nessuna dipendenza da installare: gli script usano solo la libreria standard di
Python, la pagina non carica librerie esterne. Costo di esercizio: zero.

## Come è fatto

| File | Cosa fa |
|---|---|
| `scripts/build_boundaries.py` | Scarica i confini regionali ISTAT e li semplifica per il web. Da eseguire una volta. |
| `scripts/build_hotspot_mask.py` | Individua i punti caldi permanenti (acciaierie, vulcani). Settimanale. |
| `scripts/fetch_fires.py` | Scarica i rilevamenti, li ritaglia sull'Italia e ricalcola la pagina. Giornaliero. |
| `scripts/serve.mjs` | Server statico per l'anteprima locale. |
| `docs/` | Il sito pubblicato: pagina, confini, dati. |
| `data/archive/` | Un file per giorno con tutti i rilevamenti grezzi, per le analisi future. |

## Messa in funzione

1. Crea il repository su GitHub e caricaci questa cartella:

```bash
git init && git add . && git commit -m "Prima versione" && git branch -M main
```

2. Attiva GitHub Pages: **Settings → Pages → Source: Deploy from a branch**,
   ramo `main`, cartella `/docs`.

3. Dai al workflow il permesso di scrivere: **Settings → Actions → General →
   Workflow permissions → Read and write permissions**. Senza questo il commit
   giornaliero fallisce.

4. Lancia una volta a mano **Actions → Aggiorna i dati degli incendi → Run
   workflow** per verificare che giri.

Da lì in avanti va da sé: due aggiornamenti al giorno, alle **06:45** e alle
**15:15 UTC**, e maschera dei punti caldi ogni lunedì.

Il giro del mattino è quello canonico e scrive la riga dello storico giornaliero.
Quello del pomeriggio serve a mostrare gli incendi in corso e aggiorna solo la
mappa: il workflow gli passa `AGGIORNA_STORICO=0`, altrimenti il valore di ogni
giorno dipenderebbe da quale esecuzione ha girato per ultima. Se lanci lo script
a mano la riga viene scritta, perché in quel caso è quello che ti aspetti.

Se cambi gli orari del cron, aggiorna anche la costante `AGGIORNAMENTI_UTC` in
`docs/index.html`: è quella che genera la frase "si aggiorna alle 08:45 e alle
17:15" nelle note, convertita in ora italiana a ogni caricamento così da restare
giusta anche con l'ora legale.

### Anteprima in locale

```bash
node scripts/serve.mjs
```

## Embed su Fanpage

```html
<iframe src="https://TUO-UTENTE.github.io/IncendiFanPage/"
        width="100%" height="1400" frameborder="0" scrolling="no"
        title="Gli incendi in Italia nelle ultime 24 ore"></iframe>
```

La pagina comunica la propria altezza al contenitore via `postMessage`, con un
messaggio `{ tipo: "incendi-italia-altezza", altezza: N }`. Se il CMS di Fanpage
supporta il ridimensionamento dinamico basta ascoltarlo; altrimenti l'altezza
fissa qui sopra funziona, va solo verificata su mobile dove la pagina è più
alta.

## Le scelte che contano

**I satelliti non vedono gli incendi, vedono il calore.** Senza correzioni, le
acciaierie del bresciano, la centrale di Brindisi, il polo di Ottana, i
cementifici di Gubbio e il cratere di Stromboli finiscono nel conteggio come
incendi, tutti i giorni. `build_hotspot_mask.py` li individua da sé, senza liste
scritte a mano: scarica i feed a 7 giorni e cerca i punti che si accendono
sempre nelle stesse coordinate. Un incendio brucia in un posto e non torna più,
un altoforno sì.

Un punto diventa "permanente" se si accende in almeno 4 giorni distinti, e viene
marcato `confermato` quando quei giorni si distribuiscono su almeno due
settimane. All'inizio saranno tutti `provvisorio`, perché l'archivio è giovane:
il filtro diventa più preciso man mano che il progetto gira. Nel dubbio, i punti
esclusi sono elencati in chiaro dentro `docs/latest.json`, campo
`punti_caldi_esclusi`, e le correzioni manuali si mettono in
`data/hotspot_override.json`.

**Le regioni sono ordinate per intensità, non per numero di focolai.** Dieci
bruciature di stoppie nella pianura padana non sono un'emergenza; due incendi
che alle due di notte stanno ancora bruciando in Calabria sì. L'indicatore è
l'FRP, la potenza radiativa in megawatt misurata dal satellite. Per lo stesso
motivo la pagina segnala i focolai ancora accesi di notte: un fuoco diurno e
isolato in pianura è quasi sempre agricolo, uno che continua nel buio è un
incendio che nessuno ha spento.

**Un focolaio non è un rilevamento.** Un singolo incendio accende più pixel, e
più satelliti lo vedono nello stesso giro d'orologio. I rilevamenti entro 1,5 km
vengono uniti in un focolaio, con l'FRP sommato.

**Esclusi i rilevamenti a bassa attendibilità**, in larga parte falsi positivi.
Restano tutti nell'archivio giornaliero, che conserva il dato grezzo integrale.

## Limiti da conoscere

- **La finestra di 24 ore è mobile**, quindi l'orario del cron va lasciato
  fisso: spostarlo cambia dove cade il taglio e rende i giorni dello storico non
  più confrontabili. Un anticipo di venti minuti può escludere un intero
  passaggio satellitare del giorno prima. I satelliti sorvolano l'Italia in due
  finestre, 00:00-03:00 e 10:00-14:00 UTC; nelle ore 05, 06, 15, 16, 17, 21 e 22
  non passa nessuno, e sono quelle in cui conviene tagliare. Entrambi gli orari
  scelti cadono in uno di quei buchi.
- **Il giro delle 15:15 UTC può tagliare la coda del sorvolo di mezzogiorno.**
  Il picco dei passaggi è alle 12:00 UTC ma la fascia si chiude verso le 14:00, e
  con due o tre ore di ritardo quei rilevamenti non sono ancora pubblicati alle
  15:15. Non intacca lo storico, che lo scrive solo il giro del mattino: vuol
  dire solo che la mappa del pomeriggio è ferma a un'ora o due prima. Spostando
  il secondo giro alle 17:45 UTC il problema sparirebbe, al prezzo di una
  pubblicazione più tarda.
- **L'ora italiana mostrata in pagina si sposta di un'ora** tra estate e
  inverno, perché il cron è in UTC. È il prezzo da pagare per avere giorni
  confrontabili, ed è il verso giusto del compromesso.
- **I dati sono near real-time**, con circa 3 ore di ritardo dal passaggio del
  satellite. Un incendio scoppiato stamattina alle 7 potrebbe non esserci
  ancora.
- **Le nuvole nascondono il fuoco.** Un giorno con pochi focolai può essere un
  giorno coperto, non un giorno tranquillo.
- **VIIRS vede 375 metri, MODIS un chilometro**: gli incendi piccoli o sotto
  chioma sfuggono. Questi numeri sono un minimo, non un totale.
- **GitHub Pages** ha un limite indicativo di 100 GB di traffico al mese: con
  una pagina da circa 250 KB sono nell'ordine delle 400.000 visualizzazioni
  mensili. Oltre, si sposta su Cloudflare Pages o Netlify senza cambiare nulla
  del resto.
- **I cron di GitHub Actions** possono partire con 5-30 minuti di ritardo, e i
  workflow schedulati vengono disattivati dopo 60 giorni di inattività del
  repository. Il commit giornaliero dovrebbe bastare a tenerli vivi; se arriva
  la mail di avviso, si riattivano con un clic dalla scheda Actions.
- **La MAP_KEY FIRMS non serve.** Il progetto usa i feed pubblici, senza chiave
  e senza limiti di transazione. Se ne aggiungi una come secret `FIRMS_MAP_KEY`,
  gli script passano all'API ufficiale, che ritaglia l'area a monte ed è la
  fonte da preferire per recuperare lo storico passato.

## Fonti

- Rilevamenti: [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/), sensori
  VIIRS (Suomi-NPP, NOAA-20, NOAA-21) e MODIS (Terra, Aqua).
- Confini amministrativi:
  [openpolis/geojson-italy](https://github.com/openpolis/geojson-italy), da dati
  ISTAT, CC-BY 4.0.
