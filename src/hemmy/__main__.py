"""Entry point del package: `python -m hemmy`.

Carica le variabili d'ambiente dal file .env, avvia la CLI e delega
l'esecuzione all'interfaccia a riga di comando.
"""

import sys

from dotenv import load_dotenv


def main() -> None:
    """Punto di ingresso dell'applicazione.

    - Carica .env (credenziali DeepSeek + Azure).
    - `python -m hemmy`         → CLI a riga di comando.
    - `python -m hemmy --web`   → UI web locale (FastAPI + browser).
    """
    load_dotenv()

    argv = sys.argv[1:]
    if "--web" in argv:
        # Import ritardato per assicurare che l'env sia già caricato.
        from hemmy.interfaces.web import serve

        # `port=None` di default: lascia decidere a `serve()`, che legge la
        # env var PORT (impostata da Cloud Run/qualunque container) e ripiega
        # su 8765 solo in locale. Prima questo wrapper forzava sempre 8765,
        # ignorando PORT anche in un ambiente a container — bug per Cloud Run.
        port = None
        if "--port" in argv:
            try:
                port = int(argv[argv.index("--port") + 1])
            except (ValueError, IndexError):
                pass
        serve(port=port)
        return

    from hemmy.interfaces.cli import run_cli

    run_cli()


if __name__ == "__main__":
    main()
