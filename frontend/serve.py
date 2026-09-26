"""Server statico minimale per il frontend di Hemmy (HTML/CSS/JS puro, nessuna
build). Serve SOLO file statici da questa cartella: non parla con Supabase, non
tocca l'agente, non ha permessi su nulla — è deployabile come servizio Cloud Run
completamente separato dal backend (`adf-agent-web`).

In produzione qualunque host statico va bene allo stesso modo (nginx, Cloud Storage
+ CDN, Netlify/Vercel, un container nginx): questo script è pensato per lo sviluppo
locale e per un deploy Cloud Run "leggero" senza dover configurare nginx a mano.

Uso:
    python serve.py [porta]           # default 8080, come si aspetta Cloud Run

Configurazione del backend: modifica `config.js` (o sovrascrivilo al deploy) per
puntare all'URL pubblico del servizio backend.
"""

from __future__ import annotations

import http.server
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=_DIR, **kwargs)

    def end_headers(self) -> None:
        # Nessun dato sensibile qui dentro (solo HTML/CSS/JS pubblici): cache breve
        # per non dover invalidare manualmente ad ogni deploy.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", "8080"))
    with http.server.ThreadingHTTPServer(("0.0.0.0", port), _Handler) as httpd:
        print(f"Frontend Hemmy servito su http://0.0.0.0:{port} (cartella: {_DIR})")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
