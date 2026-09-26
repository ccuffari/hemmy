# Deploy su Google Cloud Run

Hemmy si distribuisce come **due servizi Cloud Run indipendenti**:

- **`hemmy-backend`** — API FastAPI (JSON + SSE), l'agente, i tool, l'auth.
  Build: `Dockerfile` alla radice del repo.
- **`hemmy-frontend`** — HTML/CSS/JS statico puro (nessuna build, nessuna
  dipendenza dal backend a build-time). Build: `frontend/Dockerfile`.

Sono disaccoppiati di proposito: scalano, si aggiornano e si mappano su domini
diversi (es. `portal.hemmy.it` per il frontend, `api.hemmy.it` per il backend)
senza che un deploy dell'uno richieda un deploy dell'altro.

## 0. Perché due filesystem effimeri cambiano tutto

Ogni istanza Cloud Run parte da un filesystem vuoto e può essere distrutta in
qualunque momento (scale-to-zero, nuova revisione, riavvio per manutenzione).
**Nessuno stato scritto su disco dentro il container sopravvive.** Questo è
già stato tenuto in conto nel design di Hemmy, ma va **configurato** esplicitamente
al deploy:

| Variabile | Obbligatoria? | Perché |
|---|---|---|
| `SUPABASE_URL` | **Sì** in produzione | Senza, l'app ripiega su SQLite locale (`data/users.db`): sparirebbe ad ogni riavvio/scale-out, e ogni istanza vedrebbe utenti diversi. |
| `SUPABASE_SERVICE_ROLE_KEY` | Sì (con `SUPABASE_URL`) | Client Postgres lato server, bypassa la RLS per le operazioni di sistema (audit, plugin). |
| `SUPABASE_JWT_SECRET` | Sì (con `SUPABASE_URL`) | Verifica firma dei JWT emessi da Supabase Auth (login utenti). |
| `SECRETS_MASTER_KEY` | **Sì** in produzione | Chiave di cifratura (NaCl SecretBox, 32 byte base64) per tutti i segreti per-utente (API key LLM, token Airflow/Databricks/dbt/ecc.). Senza questa env var il processo ne genera una nuova ad ogni avvio: i segreti già cifrati con la chiave precedente diventano illeggibili per sempre. **Genera una sola volta e non perderla mai**: `python3 -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"` |
| `ALLOWED_ORIGINS` | Consigliata | CORS: origini autorizzate a chiamare il backend. Di default include già `portal.hemmy.it`/`admin.hemmy.it` e, via regex, qualunque `*.run.app`/`*.web.app`/`*.firebaseapp.com` — utile prima di mappare un dominio custom. |

Senza `SUPABASE_URL`+`SECRETS_MASTER_KEY` il servizio **funziona** (non crasha),
ma NON è pronto per un uso multi-utente reale: ogni istanza avrebbe il proprio
DB SQLite locale e i segreti cifrati non sopravvivrebbero a un riavvio.

## 1. Prerequisiti una-tantum

```bash
gcloud auth login
gcloud config set project IL-TUO-PROGETTO
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com
```

Crea i segreti in Secret Manager (nomi usati da `deploy/cloudrun-deploy.sh`,
personalizzabili):

```bash
# Genera SECRETS_MASTER_KEY una volta sola, conservala anche fuori da GCP
# (password manager del team) — se si perde, TUTTI i segreti utente cifrati
# diventano illeggibili e vanno reinseriti da ogni utente.
python3 -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())" \
  | gcloud secrets create hemmy-secrets-master-key --data-file=-

echo -n "IL-TUO-SUPABASE-SERVICE-ROLE-KEY" | \
  gcloud secrets create hemmy-supabase-service-role-key --data-file=-

echo -n "IL-TUO-SUPABASE-JWT-SECRET" | \
  gcloud secrets create hemmy-supabase-jwt-secret --data-file=-
```

`SUPABASE_JWT_SECRET` si trova in Supabase → Settings → API → JWT Settings.

## 2. Deploy

```bash
PROJECT_ID=il-tuo-progetto \
REGION=europe-west1 \
SUPABASE_URL=https://xxxxx.supabase.co \
./deploy/cloudrun-deploy.sh
```

Lo script:
1. Builda e distribuisce `hemmy-backend` da `Dockerfile` (root), collegando i
   3 segreti sopra + `SUPABASE_URL`/`ALLOWED_ORIGINS` come env var dirette.
2. Builda e distribuisce `hemmy-frontend` da `frontend/Dockerfile`.
3. Stampa gli URL `*.run.app` assegnati e i passi successivi (vedi sotto).

## 3. Dopo il primo deploy

- **Se backend e frontend NON condividono un dominio custom**: `config.js`
  del frontend, in produzione, punta di default a `location.origin` (stesso
  host della pagina) — sbagliato se sono due URL `*.run.app` diversi. Aggiungi
  PRIMA di `config.js` in `frontend/index.html` e `frontend/login.html`:
  ```html
  <script>window.HEMMY_API_BASE = "https://hemmy-backend-xxxx.a.run.app";</script>
  ```
  e ridistribuisci il frontend. Se invece mappi un dominio custom unico con
  path/subdomain diversi (es. `portal.hemmy.it` per il frontend che chiama
  `api.hemmy.it`), fai lo stesso ma con l'URL definitivo.
- **CORS**: il regex in `web.py` copre già `*.run.app`; se usi domini custom
  diversi da `portal.hemmy.it`/`admin.hemmy.it`, aggiungili a `ALLOWED_ORIGINS`
  (CSV) al prossimo deploy del backend.
- **Domini custom**: `gcloud run domain-mappings create --service=hemmy-frontend
  --domain=portal.hemmy.it --region=$REGION` (richiede verifica del dominio in
  Search Console). Stesso comando per il backend con `api.hemmy.it`.
- **Scaling**: `--min-instances=0` (default nello script) vuol dire cold start
  sulla prima richiesta dopo un periodo di inattività — accettabile per un
  primo lancio; alza a `1` per eliminarlo (costo continuo di un'istanza sempre
  attiva).

## 4. Verifica post-deploy

```bash
curl -s "$(gcloud run services describe hemmy-backend --region=$REGION --format='value(status.url)')/api/health" \
  || echo "nessun endpoint /api/health: verifica manualmente con l'app"
```

Login end-to-end dal frontend distribuito è il test più affidabile: se il
JWT Supabase viene verificato e `/api/ask` risponde, `SUPABASE_URL`/
`SUPABASE_JWT_SECRET`/CORS sono tutti configurati correttamente.

## 5. Cosa NON serve nell'immagine (già escluso da `.dockerignore`)

`.env`, `data/` (SQLite locale, master key locale, plugin per-utente locali),
`logs/`, `infra/` (Terraform del prodotto per i clienti, non l'infra di Hemmy),
`tests/`, `docs/`. Se in locale hai generato `config/.master.key` o
`config/backend.yaml`, restano SOLO sulla tua macchina: in Cloud Run la master
key deve essere `SECRETS_MASTER_KEY` (env var/secret), mai un file.
