# Hemmy — Documentazione completa

_Agente AI per Data Engineering & Data Architecture su Azure._
_Ultimo aggiornamento: 2026-09-17 · Python 3.10 · 106 tool · 72 test._

---

## 1. Panoramica

**ADF Agent** è un agente AI da riga di comando che aiuta a **verificare, implementare,
gestire e documentare** piattaforme dati su Azure — non solo singole pipeline Azure Data
Factory, ma l'intera Data Platform: storage, database, networking, Key Vault, RBAC,
Infrastructure as Code (Terraform) e CI/CD (GitHub Actions / Azure DevOps).

Caratteristiche portanti:

- **LLM**: DeepSeek (API compatibile OpenAI).
- **Loop ReAct**: Thought → Action → Observation → Final Answer, con contesto persistente
  tra i turni.
- **IaC-first**: il provisioning strutturale passa da Terraform (modulare, multi-ambiente),
  non da azioni imperative "a mano".
- **Sicurezza a più livelli**: identità personale, segreti mai esposti all'LLM, referenze
  Key Vault, anonimizzazione dei nomi, approvazione umana su ogni scrittura, audit log.
- **Documentazione vivente**: ogni modifica aggiorna automaticamente lo stato
  dell'infrastruttura con diagramma Mermaid.

---

## 2. Architettura

```
hemmy/
├── config/                     # agent.yaml, guardrails.yaml, prompts.md, cicd.yaml, backend.yaml*
├── infra/                      # working dir Terraform (providers.tf, moduli/ambienti)
├── docs/                       # INFRASTRUCTURE.md (vivente) + AGENTE.md (questo)
├── logs/                       # audit.log*
└── src/hemmy/
    ├── core/agent.py           # loop ReAct, contesto persistente, resilienza LLM
    ├── guardrails/guardrails.py# policy read/write, approvazione umana, anteprime
    ├── memory/memory.py        # memoria di sessione
    ├── audit/audit.py          # audit log delle scritture
    ├── docs/docgen.py          # documentazione vivente (stato + Mermaid)
    ├── infra/clients.py        # client Azure (lazy) + autenticazione personale
    ├── interfaces/cli.py       # CLI, preflight, registrazione tool, persistenza sessione
    ├── tools/                  # i tool, uno per dominio (adf, blob, sql, iac, cicd, ...)
    └── utils/helpers.py        # logger, retry, redaction segreti, secret provider
```
(*) file generati a runtime, esclusi da git.

### Componenti chiave

| Componente | Responsabilità |
|---|---|
| **Core (ReAct)** | Pianifica con l'LLM, esegue tool, mantiene la conversazione, applica anonimizzazione e redaction, resiliente ai timeout LLM. |
| **Guardrails** | Distingue read/write, richiede approvazione umana sulle scritture, mostra anteprime, blocca keyword SQL configurabili. |
| **Tools** | Funzioni deterministiche che chiamano gli SDK Azure / Terraform / Git / GitHub. Un dominio = una cartella foglia. |
| **Infra/clients** | Costruzione **lazy** dei client (nessuna connessione all'avvio) + credenziale personale. |
| **Security/anonymizer** | Tabella privata reale↔alias; i nomi non raggiungono mai l'LLM. |
| **Audit** | Traccia su file ogni azione di scrittura (redatta). |
| **Docs (docgen)** | Stato infrastruttura + `INFRASTRUCTURE.md` con Mermaid, aggiornato ad ogni modifica. |

---

## 3. Modello di autenticazione

**Identità personale, non Service Principal** (default `AZURE_AUTH_MODE=user`):

- Azure: `az login` (Azure CLI) → fallback browser interattivo. L'agente eredita
  **permessi e responsabilità del singolo utente** (architect/engineer/analyst). I
  fallimenti RBAC sono corretti e voluti: riflettono il ruolo reale.
- GitHub: `gh auth login` (GitHub CLI). Il token è recuperato **al volo** da
  `gh auth token`, tenuto in memoria per la singola operazione, **mai** salvato nel `.env`.
- Service Principal disponibile solo come opzione (`AZURE_AUTH_MODE=service_principal`) per
  CI/automazione.

**Preflight all'avvio** (obbligatorio): verifica di potersi connettere a
1. **Azure** (autenticazione personale),
2. **Remote backend Terraform** (storage dello state; se manca, guida la configurazione),
3. **CI/CD** (GitHub/DevOps).
Le connessioni (autenticazione) devono esserci per operare; i permessi puntuali (RBAC)
restano dell'utente.

> Distinzione fondamentale: **autenticazione** (posso connettermi) ≠ **autorizzazione**
> (ho i permessi per quella specifica azione). Il preflight controlla la prima; la seconda
> fallisce correttamente sul singolo tool se manca.

---

## 4. Modello di sicurezza (a più livelli)

La sicurezza è il cuore del progetto. Livelli sovrapposti:

1. **Identità personale** — niente SP onnipotente; ogni utente opera con i propri permessi.
2. **Segreti mai all'LLM** — password, username, connection string, chiavi, token non
   vengono mai passati al modello:
   - I client verso le risorse predefinite vivono nel processo Python; le credenziali non
     entrano nei messaggi.
   - Per risorse arbitrarie il segreto lo inserisce l'operatore in input sicuro
     (`getpass`), tramite `*_for_account` / `*_for_connection`; il valore resta locale alla
     funzione e non viene mai restituito.
   - **Cache di sessione dei segreti**: il segreto per una risorsa è chiesto una sola volta
     per sessione (in-process), riducendo i re-prompt.
3. **Redaction** — ogni Observation e ogni log passano da `redact_secrets()` che maschera
   connection string, AccountKey, password, SAS, API key `sk-…`, token GitHub, URL con
   token. Rete di sicurezza contro leak accidentali.
4. **Referenze Key Vault** — il codice Terraform non contiene mai segreti in chiaro: le
   credenziali (es. SQL) sono `data "azurerm_key_vault_secret"`. Se manca, si crea prima
   con `keyvault.set_secret` (valore dell'operatore).
5. **Secrets della pipeline via agente** — i GitHub Actions secret (`ARM_*`, ecc.) si
   definiscono con `github.set_secret`: valore dell'operatore, **cifrato** (libsodium
   sealed box) prima dell'invio, mai esposto al modello.
6. **Anonimizzazione dei nomi** — una tabella privata (mai all'LLM) mappa i nomi reali
   delle risorse ad alias generici (`kviaagentwe01` → `<vault1>`). Il modello vede solo
   alias; gli args tornano nomi reali prima dell'esecuzione; la risposta all'utente riporta
   i nomi reali. Ulteriore barriera anti data-leak.
7. **Human-in-the-loop** — ogni azione di scrittura richiede approvazione umana esplicita a
   terminale, con **anteprima** di cosa cambierà (piano Terraform, risorse da spostare,
   comando pipeline, file da committare). L'operatore vede i **nomi reali**.
8. **Audit log** — ogni scrittura (successo/errore/negata) è tracciata in `logs/audit.log`
   con utente, timestamp, azione, args redatti.
9. **Guardrail SQL configurabile** — `always_deny_sql` in `guardrails.yaml` può vietare in
   modo assoluto keyword (default: vuoto → tutto consentito con approvazione).

---

## 5. Guardrail e approvazioni

- **Read** (61 tool): eseguiti senza approvazione.
- **Write** (45 tool): richiedono conferma umana (`si`/`no`) a terminale.
- **Anteprime al gate**: `iac.apply` mostra il piano Terraform; `iac.destroy` le risorse in
  distruzione; `resource.move_resources` l'elenco+avvisi; `cicd.trigger_pipeline` il
  comando; `github.commit_files`/`github.set_secret` i file/segreti coinvolti.
- **Validazione SQL**: le operazioni distruttive su `sql.execute_write*` sono governate da
  `always_deny_sql`; match su confini di parola (nessun falso positivo tipo `id_insert`).

---

## 6. Logica operativa (loop ReAct)

1. La domanda utente viene **anonimizzata** e aggiunta alla conversazione persistente.
2. L'LLM produce `Thought` + `Action` + `Action Input` (JSON anche annidato) oppure
   `Final Answer`.
3. Gli **args vengono de-anonimizzati** (alias → reali) e passati al guardrail (l'operatore
   approva vedendo i nomi reali), poi al tool.
4. Il risultato viene **redatto** dai segreti e **anonimizzato** nei nomi prima di tornare
   all'LLM come `Observation`.
5. Alla `Final Answer`, i nomi reali vengono ripristinati per l'utente.

Caratteristiche di robustezza:
- **Contesto persistente** tra i turni (finestra scorrevole `max_history`), salvato su
  `.session.json` e ricaricabile; comando `reset` per azzerare.
- **Resilienza LLM**: retry su timeout/connessione/rate-limit; un errore LLM non crasha
  l'app (messaggio grazioso, contesto preservato).
- **Client lazy**: nessuna connessione all'avvio; ogni servizio è contattato on-demand.

---

## 7. IaC-first: struttura Terraform modulare e multi-ambiente

**Regola inderogabile**: niente `.tf` monolitico. Struttura obbligatoria:

```
modules/<risorsa>/         main.tf · variables.tf · outputs.tf
environments/{dev,uat,prod}/ providers.tf · backend.tf · locals.tf · variables.tf · main.tf · terraform.tfvars
```

- `iac.scaffold_module` genera il modulo di una risorsa (+ il `module_call` da inserire
  nell'ambiente).
- `iac.scaffold_environment` genera lo scheletro di un ambiente (state per-ambiente:
  `key = "<env>/terraform.tfstate"`).
- Il **remote backend** (state su Azure Storage) è passo obbligatorio all'avvio; container
  `tfstate` creato automaticamente con la connection string fornita dall'operatore (non
  persistita).

### Flusso end-to-end (richiesta risorsa)
```
iac.scaffold_module (+ scaffold_environment se manca)
  → github.commit_files (moduli + ambiente + workflow, un commit)   [oppure iac.write_file locale]
  → cicd.trigger_pipeline plan → cicd.wait_for_run → (fail) cicd.get_run_logs → patch
  → cicd.trigger_pipeline apply (approvazione) → cicd.wait_for_run
  → verifica su Azure (resource.list_resources, ...)
```
In alternativa, esecuzione locale con identità personale: `iac.init/plan/apply`.

---

## 8. CI/CD

- Provider **configurabile** in `config/cicd.yaml` (`github` | `azure_devops`).
- `cicd.generate_pipeline` / `cicd.render_pipeline` producono la pipeline Terraform
  (init/validate/plan/apply/destroy via `workflow_dispatch` / parametro).
- `cicd.trigger_pipeline` avvia; `cicd.wait_for_run` attende l'esito; `cicd.get_run_jobs` e
  `cicd.get_run_logs` diagnosticano i fallimenti (download log con gestione del redirect
  firmato, senza header di auth).
- Scrittura su GitHub **senza working copy** via `github.commit_files` (Git Data API, commit
  atomico; bootstrap automatico su repo vuoto).

> **CI senza credenziali**: per far girare `apply` sul runner senza secret ARM_* serve
> **OIDC / Workload Identity Federation** (roadmap). Il resto del giro funziona con login
> personale.

---

## 9. Documentazione vivente

Ad ogni azione di scrittura riuscita, `DocStore` aggiorna uno stato strutturato e rigenera
`docs/INFRASTRUCTURE.md`: inventario (RG, storage, SQL, ADF, networking) **allo stato
attuale** (non lo storico) + **diagramma Mermaid**. `docs.sync` ricostruisce lo stato
leggendo l'infrastruttura reale da Azure; `docs.add_note`/`remove_note` aggiungono note
custom persistenti.

---

## 10. Catalogo dei tool (106: 61 read · 45 write)

Legenda: **[W]** = scrittura (richiede approvazione umana). Gli altri sono in lettura.

### ADF — Azure Data Factory (24)
`list_pipelines`, `get_pipeline`, `get_pipeline_runs`, `get_activity_runs`, `get_triggers`,
`list_linked_services`, `get_linked_service`, `list_datasets`, `get_dataset`,
`get_factory_identity`, `list_managed_private_endpoints` ·
**[W]** `create_linked_service`, `create_linked_service_kv`, `create_keyvault_linked_service`,
`create_dataset`, `create_pipeline`, `create_copy_pipeline`, `delete_linked_service`,
`delete_dataset`, `delete_pipeline`, `trigger_pipeline_run`, `run_and_wait`,
`create_managed_vnet`, `create_managed_private_endpoint`.

### Blob / Storage (11)
`list_storage_accounts`, `list_containers`, `list_containers_for_account`, `list_blobs`,
`list_blobs_for_account`, `get_blob_schema`, `get_blob_schema_for_account` ·
**[W]** `create_container`, `delete_container`, `upload`, `create_storage_account`.

### Table / Queue / File Share (11)
`table.list_tables`, `queue.list_queues`, `fileshare.list_shares`,
`fileshare.list_directories_and_files` ·
**[W]** `table.create_table`, `table.delete_table`, `queue.create_queue`,
`queue.delete_queue`, `fileshare.create_share`, `fileshare.create_directory`,
`fileshare.delete_share`.

### SQL — data-plane e management (12)
`list_tables`, `get_table_schema`, `get_row_count`, `list_tables_for_connection`,
`get_table_schema_for_connection`, `get_row_count_for_connection`, `list_sql_servers`,
`list_sql_databases` ·
**[W]** `execute_write`, `execute_write_for_connection`, `create_sql_server`,
`create_sql_database`.

### Resource group / Resource move (4)
`list_resource_groups`, `list_resources` · **[W]** `create_resource_group`, `move_resources`.

### Key Vault (4)
`list_vaults`, `list_secrets` · **[W]** `set_secret`, `delete_secret`.

### RBAC (3)
`list_role_assignments` · **[W]** `assign_role`, `remove_role_assignment`.

### Networking (4)
`list_vnets`, `list_subnets`, `list_private_endpoints`, `list_nsgs`.

### Management Lock (3)
`list_locks` · **[W]** `create_lock`, `delete_lock`.

### IaC — Terraform (14)
`init`, `validate`, `plan`, `show`, `state_list`, `output`, `write_file`, `scaffold`,
`scaffold_module`, `scaffold_environment` ·
**[W]** `apply`, `destroy`, `import`, `configure_remote_backend`.

### CI/CD (7)
`generate_pipeline`, `render_pipeline`, `get_status`, `wait_for_run`, `get_run_jobs`,
`get_run_logs` · **[W]** `trigger_pipeline`.

### GitHub (3)
`get_default_branch` · **[W]** `commit_files`, `set_secret`.

### Git locale (3)
`status` · **[W]** `commit`, `push`.

### Docs — documentazione vivente (3)
`sync`, `add_note`, `remove_note`.

---

## 11. Configurazione

| File | Contenuto |
|---|---|
| `config/agent.yaml` | modello LLM, temperature, `max_iterations`, `timeout`, `max_retries`, `max_history`. |
| `config/guardrails.yaml` | azioni read consentite, azioni write (approvazione), `always_deny_sql`, limiti. |
| `config/prompts.md` | System / Planner / Guardrail prompt (regole di sicurezza e IaC). |
| `config/cicd.yaml` | provider CI/CD e impostazioni (repo/branch/workflow, org/project/pipeline). |
| `config/backend.yaml` | (runtime) storage account/container/key del remote backend. |
| `.env` | solo `DEEPSEEK_API_KEY` (unico segreto), config non segreta Azure/GitHub. |

### Variabili `.env` (con login personale)
```
DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
AZURE_AUTH_MODE=user · AZURE_TENANT_ID · ADF_SUBSCRIPTION_ID · ADF_RESOURCE_GROUP · ADF_FACTORY_NAME
GITHUB_REPO
```
Non servono `AZURE_CLIENT_*` (solo con `service_principal`), né token/connection string.

---

## 12. Setup e prerequisiti

```bash
# dipendenze
python -m venv .venv && .venv\Scripts\activate
pip install -e .

# strumenti esterni
az login           # identità personale Azure
gh auth login      # identità personale GitHub
terraform -version # Terraform CLI nel PATH
# + Microsoft ODBC Driver 18 for SQL Server (per i tool SQL data-plane)

# avvio
python -m hemmy
```
All'avvio: preflight (Azure/backend/CI-CD) → se il backend manca, configurazione guidata.

---

## 13. Testing

Suite `tests/test_agent.py` (72 test): guardrail (read/write/approvazione, DROP/INSERT,
falsi positivi), parsing ReAct (JSON annidato), redaction, cache segreti, schema_diff,
persistenza contesto/reset, audit, documentazione vivente, IaC (write_file, scaffold
module/environment, backend, import), CI/CD (generazione, trigger, wait, log), GitHub
(commit multi-file, bootstrap repo vuoto, set_secret cifrato), lazy client, autenticazione
(user vs SP), anonimizzazione (roundtrip, registrazione, integrazione end-to-end).
```bash
python -m pytest -q      # 72 passed
```

---

## 14. Limitazioni note (oneste)

- **CI senza secret**: l'`apply` sul runner GitHub richiede OIDC/Workload Identity
  Federation (roadmap); finora il giro completo senza secret si valida in locale.
- **Anonimizzazione**: copre i nomi **noti** (dai risultati dei tool o dagli args). Un nome
  del tutto nuovo digitato dall'utente al primo turno può raggiungere l'LLM prima di essere
  registrato (basso rischio: è un nome scelto dall'utente, non inventario esistente).
- **Redaction**: best-effort su pattern noti; la difesa primaria è che i segreti non sono
  restituiti dai tool.
- **Parsing SQL DDL** per la doc vivente: best-effort (CREATE/DROP/rename tabella); ALTER di
  colonne complessi non sono riflessi nel dettaglio.
- **Networking privato & runner**: un runner GitHub-hosted non raggiunge storage/risorse in
  VNet privata; serve self-hosted runner o allowlist (scelta d'architettura).
- **Terraform locale multi-ambiente**: i comandi `iac.*` operano su una working dir; per
  eseguire per-ambiente (`environments/<env>`) conviene un parametro dir dedicato (roadmap).
- **Stato/segreti locali**: `.session.json`, `.anonymizer.json`, `config/backend.yaml`,
  `logs/` sono locali e in `.gitignore`; contengono dati sensibili → tenerli privati.

---

## 15. Roadmap

- **OIDC / Workload Identity Federation** per la CI senza segreti.
- Parametro `environment` per i comandi Terraform locali (run per-ambiente).
- Tool RBAC/secret self-service completi per bootstrap (SP CI → Storage Blob Data
  Contributor, secrets ARM_* via `github.set_secret`).
- Estensione anonimizzazione ai nomi nella domanda utente (euristica opzionale).
- Backend Terraform remoto con state locking documentato per team.

---

## 16. Principi di design

1. **1 file = 1 componente** per cartella foglia; domini separati.
2. **IaC-always**: la piattaforma si costruisce come codice, non a mano.
3. **Sicurezza per difetto**: identità personale, segreti fuori dall'LLM, approvazione
   umana, audit, anonimizzazione.
4. **Onestà operativa**: l'agente diagnostica, cita evidenze, propone patch concrete e
   dichiara i limiti dei propri controlli.
5. **Allineamento verificato**: tool registrati ↔ documentati ↔ in policy sempre coerenti
   (controllo automatico).
