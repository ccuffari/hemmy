# Hemmy

Agente AI per la **diagnosi e il monitoraggio** di pipeline Azure Data Factory.

L'agente usa **DeepSeek** (API compatibile OpenAI) come LLM e opera in sola lettura su:

- **Azure Data Factory** — metadati delle pipeline
- **Azure Blob Storage** — schema dei file sorgente
- **Azure SQL Database** — schema delle tabelle di destinazione

## Architettura

Principio di design: **1 file = 1 componente**. Ogni cartella foglia contiene esattamente
un file di codice.

```
hemmy/
├── config/                 # Configurazione (YAML + prompt)
├── src/hemmy/
│   ├── core/               # Loop ReAct (planner + executor + state)
│   ├── memory/             # Memoria breve e lungo termine
│   ├── guardrails/         # Policy read-only/write + approvazione umana
│   ├── tools/              # Tool ADF / Blob / SQL
│   ├── infra/              # Client SDK + autenticazione
│   ├── interfaces/         # CLI
│   └── utils/              # Logger, schema_diff, retry
└── tests/                  # Test
```

## Setup

1. Installa le dipendenze:
   ```bash
   pip install -e .
   ```
2. Copia le tue credenziali nel file `.env` (già presente con placeholder):
   - Ottieni la API key DeepSeek su https://platform.deepseek.com/api_keys
   - Sostituisci `your-api-key-here` con la chiave reale
   - Compila le credenziali Azure (Service Principal) e le connection string
3. Verifica che `.env` sia escluso dal versionamento (già in `.gitignore`).

## Uso

```bash
python -m hemmy
```

## Modello

Modello consigliato per l'uso con agenti: **`deepseek-flash`**.
Configurabile in `config/agent.yaml` e via variabile `DEEPSEEK_MODEL` nel `.env`.

## Web UI multi-utente (Hemmy)

`python -m hemmy --web` avvia il backend FastAPI (`hemmy-web`), un'API JSON +
SSE pura: **non serve più pagine HTML**. Il frontend statico vive sotto `frontend/`
(HTML/CSS/JS puro, nessuna build) ed è un servizio separato e indipendentemente
deployabile — in locale: `python frontend/serve.py`.

Sessione: bearer token (non cookie), perché FE e BE possono girare su origin diversi.
Il frontend lo salva in `localStorage` dopo il login e lo manda come header
`Authorization: Bearer <token>` su ogni chiamata; configuralo puntando al backend
giusto in `frontend/config.js` (`window.HEMMY_API_BASE`).

### Persistenza: Supabase (opzionale) o SQLite locale

Se `SUPABASE_URL` non è impostata, tutto (utenti, chat, settings, segreti, tool
generati a runtime, audit-log) resta su SQLite/file locali — zero setup, comportamento
storico invariato. Per una persistenza condivisa multi-istanza (es. Cloud Run):

1. Esegui `supabase/schema.sql` sul progetto Supabase (SQL editor o `supabase db push`).
2. Imposta le variabili d'ambiente:
   - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`
     (dalla dashboard Supabase → Project Settings → API)
   - `SECRETS_MASTER_KEY` — base64 di 32 byte, FISSA (non generata al volo): cifra i
     segreti applicativi per-utente. Su filesystem effimero (Cloud Run) è obbligatoria,
     altrimenti i segreti già cifrati diventano illeggibili ad ogni restart.
   - `ALLOWED_ORIGINS` — origin del frontend separati da virgola (CORS), es.
     `https://hemmy-frontend-xxxx.run.app`. Default `*` (solo per sviluppo).

Il login usa Supabase Auth (email+password); lo username resta un campo separato in
`profiles`. Registrazione ed emissione token gestite direttamente dal backend
(`auth/supabase_users.py`), nessuna configurazione aggiuntiva lato Supabase Auth
richiesta oltre l'aver eseguito lo schema.

### OAuth verso i provider cloud (Azure / GitHub)

Riguarda l'identità con cui l'agente OPERA su Azure/GitHub (permessi ereditati
dall'utente, mai un Service Principal condiviso) — indipendente dal login all'app:
- Azure: nessun setup di default (usa il client pubblico di Azure CLI); per una App
  Registration propria imposta `AZURE_OAUTH_CLIENT_ID`.
- GitHub: richiede una OAuth App con "Device Flow" abilitato
  (github.com/settings/developers, nessun client secret) → `GITHUB_OAUTH_CLIENT_ID`.

Nessuno di questi token viene mai persistito (né su SQLite né su Supabase): vivono
solo in memoria di processo, per-utente, finché il server resta attivo o l'utente
si disconnette esplicitamente.
