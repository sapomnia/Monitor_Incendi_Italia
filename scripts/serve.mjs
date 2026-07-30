// Server statico minimo per l'anteprima locale della pagina.
// In produzione il sito e servito da GitHub Pages: questo serve solo a
// sviluppare, perche la pagina carica i dati via fetch e da file:// non
// funzionerebbe.
//
//     node scripts/serve.mjs [porta]

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const RADICE = resolve(dirname(fileURLToPath(import.meta.url)), "..", "docs");
const PORTA = Number(process.argv[2]) || 8787;

const TIPI = {
  ".html": "text/html; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
};

createServer(async (richiesta, risposta) => {
  const percorso = decodeURIComponent(new URL(richiesta.url, "http://localhost").pathname);
  const relativo = normalize(percorso === "/" ? "/index.html" : percorso).replace(/^(\.\.[/\\])+/, "");
  const file = join(RADICE, relativo);

  if (!file.startsWith(RADICE)) {
    risposta.writeHead(403).end("Vietato");
    return;
  }

  try {
    const contenuto = await readFile(file);
    risposta.writeHead(200, {
      "Content-Type": TIPI[extname(file)] || "application/octet-stream",
      "Cache-Control": "no-cache",
    });
    risposta.end(contenuto);
  } catch {
    risposta.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    risposta.end("Non trovato: " + relativo);
  }
}).listen(PORTA, () => {
  console.log(`Anteprima su http://localhost:${PORTA} (cartella docs/)`);
});
