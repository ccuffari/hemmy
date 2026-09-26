# Immagine del BACKEND Hemmy (FastAPI + agente), pensata per Cloud Run.
#
# Il frontend (HTML/CSS/JS puro) è un servizio Cloud Run SEPARATO e indipendente
# (vedi frontend/Dockerfile): questa immagine serve solo l'API JSON + SSE.
# `create_app()` monta comunque `frontend/` come file statici se la trova (utile
# in locale/monolitico), ma qui non la copiamo: build più leggera e più veloce.
#
# Persistenza: il filesystem del container è EFFIMERO (ogni istanza/revisione
# riparte da zero). In produzione servono OBBLIGATORIAMENTE:
#   - SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY (utenti, segreti, plugin, audit
#     log — mai solo SQLite locale, che sparirebbe ad ogni riavvio/scale-out);
#   - SECRETS_MASTER_KEY (32 byte base64: senza, ogni istanza genererebbe una
#     chiave diversa e i segreti cifrati di ogni utente diventerebbero
#     illeggibili al primo riavvio). Vedi docs/DEPLOY_CLOUD_RUN.md.

FROM python:3.11-slim-bookworm AS base

# --- Driver ODBC per SQL Server (AAD token auth) -----------------------------
# Serve ai tool sql.*/dataquality.*/mdm.* (pyodbc + "ODBC Driver 18 for SQL
# Server"). Se questi tool non vengono usati si può rimuovere questo blocco per
# un'immagine più piccola, ma di default li includiamo: sono nella baseline
# nativa versionata (native_plugins/), non plugin opzionali installati a runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg2 ca-certificates unixodbc unixodbc-dev \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list -o /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get purge -y curl gnupg2 \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer separato per le dipendenze: cache riusata finché pyproject.toml non cambia.
# README.md serve perché pyproject.toml lo referenzia come `readme` (setuptools
# lo legge per i metadati del pacchetto in fase di build).
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Codice/config necessari a runtime (NON .env, NON data/, NON frontend/: vedi
# .dockerignore — build context già filtrato, questi COPY sono ridondanti-safe).
COPY config/ ./config/

# Utente non-root: best practice per container in produzione.
RUN useradd --create-home --uid 1000 hemmy \
    && mkdir -p /app/data /app/logs \
    && chown -R hemmy:hemmy /app
USER hemmy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HEMMY_PROJECT_ROOT=/app
# OBBLIGATORIA qui: il pacchetto è installato in modo NON editable (pip
# install .), quindi vive sotto site-packages — una gerarchia disgiunta da
# dove stanno config/data/logs (copiati a fianco in /app). Senza questa env
# var, `Path(__file__).resolve().parents[N]` risale nell'albero SBAGLIATO
# (es. /usr/local/lib/python3.11) e ogni lettura di config/agent.yaml fallisce
# silenziosamente — bug reale osservato: nessun provider LLM risolvibile per
# nessun utente, storico/tool vuoti in UI senza alcun errore visibile.

# Cloud Run inietta PORT (default locale 8080 se non specificato altrove);
# `hemmy.interfaces.web.serve()` legge PORT/HOST dall'ambiente (HOST default
# 0.0.0.0, obbligatorio in un container).
EXPOSE 8080

CMD ["python", "-m", "hemmy", "--web"]
