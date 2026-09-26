#!/usr/bin/env bash
# Deploy di Hemmy (backend + frontend) su Google Cloud Run.
#
# Prerequisiti:
#   - gcloud CLI autenticato (`gcloud auth login`) con un progetto GCP attivo
#     e billing abilitato; Cloud Run + Cloud Build + Artifact Registry API
#     abilitate (`gcloud services enable run.googleapis.com cloudbuild.googleapis.com
#     artifactregistry.googleapis.com`).
#   - I segreti elencati sotto già creati in Secret Manager (vedi
#     docs/DEPLOY_CLOUD_RUN.md per i comandi `gcloud secrets create`).
#
# Uso:
#   PROJECT_ID=il-tuo-progetto REGION=europe-west1 ./deploy/cloudrun-deploy.sh
#
# Idempotente: rieseguibile ad ogni deploy (crea la revisione successiva).

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PROJECT_ID="${PROJECT_ID:?imposta PROJECT_ID (es. PROJECT_ID=mio-progetto ./deploy/cloudrun-deploy.sh)}"
REGION="${REGION:-europe-west1}"
BACKEND_SERVICE="${BACKEND_SERVICE:-hemmy-backend}"
FRONTEND_SERVICE="${FRONTEND_SERVICE:-hemmy-frontend}"

echo "==> Progetto: $PROJECT_ID | Regione: $REGION"

# --- 1. Backend --------------------------------------------------------------
# `--source .` fa buildare Cloud Build usando il Dockerfile di root.
# Segreti sensibili via Secret Manager (--set-secrets), configurazione non
# sensibile via env var dirette (--set-env-vars). Vedi docs/DEPLOY_CLOUD_RUN.md
# per la lista completa e il perché di ciascuna.
echo "==> Deploy backend ($BACKEND_SERVICE)..."
gcloud run deploy "$BACKEND_SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --source . \
  --allow-unauthenticated \
  --min-instances=0 \
  --max-instances=10 \
  --memory=512Mi \
  --set-env-vars="SUPABASE_URL=${SUPABASE_URL:?imposta SUPABASE_URL}" \
  --set-env-vars="ALLOWED_ORIGINS=${ALLOWED_ORIGINS:-https://portal.hemmy.it}" \
  --set-secrets="SUPABASE_SERVICE_ROLE_KEY=hemmy-supabase-service-role-key:latest" \
  --set-secrets="SUPABASE_JWT_SECRET=hemmy-supabase-jwt-secret:latest" \
  --set-secrets="SECRETS_MASTER_KEY=hemmy-secrets-master-key:latest"

BACKEND_URL="$(gcloud run services describe "$BACKEND_SERVICE" \
  --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
echo "==> Backend disponibile su: $BACKEND_URL"

# --- 2. Frontend ---------------------------------------------------------
# Build context = frontend/ (il suo Dockerfile è self-contained, nessuna
# dipendenza dal resto del repo). HEMMY_API_BASE_OVERRIDE sovrascrive
# `window.HEMMY_API_BASE` al build se backend e frontend NON condividono
# lo stesso dominio custom (in quel caso config.js userebbe location.origin,
# sbagliato: punterebbe al frontend stesso, non al backend).
echo "==> Deploy frontend ($FRONTEND_SERVICE)..."
gcloud run deploy "$FRONTEND_SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --source frontend \
  --allow-unauthenticated \
  --min-instances=0 \
  --max-instances=10 \
  --memory=256Mi

FRONTEND_URL="$(gcloud run services describe "$FRONTEND_SERVICE" \
  --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
echo "==> Frontend disponibile su: $FRONTEND_URL"

cat <<EOF

==> Fatto. Prossimi passi:
  1. Se backend e frontend NON condividono un dominio custom comune, aggiungi
     nel <head> di frontend/index.html e frontend/login.html, PRIMA di
     config.js:
       <script>window.HEMMY_API_BASE = "$BACKEND_URL";</script>
     e ridistribuisci il frontend.
  2. Aggiorna ALLOWED_ORIGINS sul backend per includere $FRONTEND_URL (già
     coperto in automatico dal regex per *.run.app in web.py, ma un dominio
     custom va aggiunto esplicitamente).
  3. Mappa i domini custom (portal.hemmy.it -> frontend, api.hemmy.it ->
     backend) con: gcloud run domain-mappings create.
EOF
