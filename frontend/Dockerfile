# Immagine del FRONTEND Hemmy (HTML/CSS/JS puro, nessuna build).
#
# Build context: SOLO questa cartella (`docker build -f frontend/Dockerfile
# frontend/` oppure `gcloud run deploy --source frontend`). Servizio Cloud Run
# separato e indipendente dal backend: nessuna dipendenza Python di terze parti,
# nessun accesso a Supabase/agente/segreti — serve solo file statici pubblici.
#
# IMPORTANTE prima del deploy: `config.js` punta di default a `location.origin`
# in produzione (stesso host della pagina). Se backend e frontend girano su
# domini/URL Cloud Run DIVERSI, sovrascrivi `window.HEMMY_API_BASE` — o al build
# (sed su config.js in questo Dockerfile) o servendo un config.js diverso per
# ambiente. Vedi docs/DEPLOY_CLOUD_RUN.md.

FROM python:3.11-slim-bookworm

WORKDIR /app
COPY . ./

ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python", "serve.py"]
