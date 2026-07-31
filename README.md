# Gli incendi in Italia nelle ultime 24 ore

Pagina aggiornata automaticamente ogni giorno con i focolai rilevati dai
satelliti NASA sul territorio italiano. Pensata per essere pubblicata tramite iframe.

Nessuna dipendenza da installare: gli script usano solo la libreria standard di
Python, la pagina non carica librerie esterne. Costo di esercizio: zero.

## Come è fatto

| File | Cosa fa |
|---|---|
| `scripts/build_boundaries.py` | Scarica i confini regionali ISTAT e li semplifica per il web. Da eseguire una volta. |
| `scripts/build_hotspot_mask.py` | Individua i punti caldi permanenti (acciaierie, vulcani). Settimanale. |
| `scripts/fetch_fires.py` | Scarica i rilevamenti, li ritaglia sull'Italia e ricalcola la pagina. Giornaliero. |
| `scripts/rete.py` | Scaricamento HTTP con tre tentativi e attesa crescente, usato da tutti. |
| `scripts/controlla_freschezza.py` | Verifica da quanto non riesce un aggiornamento. Due volte al giorno. |
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

Il giro del mattino copre la notte appena passata, quello del pomeriggio mostra
gli incendi in corso. Nessuno dei due è più importante dell'altro: qualunque
esecuzione, anche manuale, produce lo stesso risultato.

Se cambi gli orari del cron, aggiorna anche la costante `AGGIORNAMENTI_UTC` in
`docs/index.html`: è quella che genera la frase "si aggiorna alle 08:45 e alle
17:15" nelle note, convertita in ora italiana a ogni caricamento così da restare
giusta anche con l'ora legale.

### Anteprima in locale

```bash
node scripts/serve.mjs
```

## Embed

```html
<iframe src="https://TUO-UTENTE.github.io/IncendiXXX/"
        width="100%" height="1400" frameborder="0" scrolling="no"
        title="Gli incendi in Italia nelle ultime 24 ore"></iframe>
```

La pagina comunica la propria altezza al contenitore via `postMessage`, con un
messaggio `{ tipo: "incendi-italia-altezza", altezza: N }`. Se il CMS
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

**La serie storica si ricostruisce dall'archivio, non si accumula.** A ogni giro
`fetch_fires.py` rilegge tutti i file di `data/archive/`, elimina i doppioni
dovuti alle finestre sovrapposte e ricalcola il conteggio di ogni giorno di
calendario. Il conteggio del 30 luglio resta quindi il conteggio del 30 luglio,
chiunque lo esegua e a qualunque ora: lo scheduler di GitHub può ritardare o
saltare un'esecuzione senza lasciare buchi, perché il giro successivo rifà tutto
da capo. Entrano solo i giorni che l'archivio copre per intero — il giorno in
corso resta fuori, perché un conteggio parziale in fondo al grafico sembrerebbe
un crollo. I giorni sono in ora UTC: lo scarto con il calendario italiano è di
una o due ore e cade in piena notte, quando i satelliti non sorvolano l'Italia.

**Se i dati sono fermi, la pagina lo dice.** Oltre le 18 ore dall'ultimo
aggiornamento riuscito compare un avviso in cima. La soglia è appena sopra le 15
ore e mezza che separano il giro delle 15:15 da quello delle 06:45, così scatta
solo quando è successo qualcosa davvero.

**E c'è una rete di sicurezza contro il guasto silenzioso.** Quando lo scheduler
salta un'esecuzione non fallisce niente, quindi GitHub non manda nessuna mail:
il guasto peggiore è quello che non si annuncia. Il workflow di sorveglianza
gira alle 10:00 e alle 22:00 UTC, controlla l'età dell'ultimo aggiornamento e,
se ha superato le 18 ore, **prova prima a rimediare da solo** rilanciando la
raccolta. Solo se dopo il tentativo i dati sono ancora fermi — perché i feed
della NASA non rispondono, non perché GitHub ha saltato un giro — fallisce
apposta, e un'esecuzione fallita la mail la manda. Il recupero è innocuo proprio
perché la serie storica si ricostruisce dall'archivio: un giro in più, a
un'ora qualunque, non altera nessun numero.

**Ogni download viene ritentato tre volte** con attesa crescente, rispettando
l'eventuale `Retry-After` del server. I 502 e 503 passeggeri della NASA
altrimenti farebbero saltare un sensore per l'intera giornata. Sugli errori
definitivi come il 404 lo script molla subito, senza attese inutili. Se cade un
sensore solo, l'aggiornamento prosegue con gli altri e la pagina lo dichiara in
fondo; se cadono tutti, lo script si ferma senza scrivere e la pagina resta
all'ultimo dato buono invece di azzerarsi.

## Limiti da conoscere

- **Lo scheduler di GitHub non è puntuale, e ogni tanto salta.** Osservato di
  persona: il giro delle 15:15 del 30 luglio è partito alle 16:45, e quello
  delle 06:45 del 31 luglio non è mai partito, lasciando la pagina ferma per
  sedici ore. I workflow risultavano attivi e senza errori: è la schedulazione a
  essere "best effort". La serie storica è immune perché si ricostruisce
  dall'archivio, la freschezza della mappa no — per quella c'è l'avviso in
  pagina. Se succede spesso, l'unico rimedio vero è un secondo esecutore che
  chiami il repository dall'esterno.
- **La finestra di 24 ore della mappa è mobile.** Gli orari del cron cadono
  apposta nelle ore senza sorvoli: i satelliti passano fra le 00:00 e le 03:00 e
  fra le 10:00 e le 14:00 UTC, mentre alle 05, 06, 15, 16, 17, 21 e 22 non passa
  nessuno. Tagliare lì evita che un ritardo faccia entrare o uscire un intero
  passaggio dal conteggio mostrato in mappa. I giorni dello storico invece non
  ne risentono più, da quando si calcolano per giorno di calendario.
- **Un focolaio resta in mappa un po' più di 24 ore**, perché sparisce solo al
  primo aggiornamento che non lo comprende più: in pratica fra le 25 e le 30 ore
  a seconda di quando è stato rilevato. Il satellite non osserva mai lo
  spegnimento, quindi un punto in mappa può essere spento da un pezzo: per
  questo la cifra in cima dice "focolai rilevati" e non "attivi".
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
