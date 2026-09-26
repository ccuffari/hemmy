"""Guardrail: policy + validazione + approvazione umana.

Applica le regole definite in `config/guardrails.yaml`:
- distingue azioni read-only (consentite) da azioni di scrittura (approvazione umana)
- valida gli argomenti (es. blocco SQL sempre-vietato, limite righe)
- richiede approvazione umana esplicita prima di eseguire azioni di scrittura
  (human-in-the-loop / "man in the middle")
- protegge i domini riservati (filesystem, shell, os, sys, …): mai disponibili
  agli utenti, né come tool nativi né come plugin
- protegge i tool nativi da modifica/cancellazione/shadowing: gli utenti possono
  USARLI e LEGGERLI, ma non sovrascriverli né cancellarli
- consente agli utenti di creare/leggere/modificare/cancellare i PROPRI plugin,
  purché non violino i domini riservati né collidano con nomi di tool nativi
- valida staticamente il codice dei plugin proposti (import/chiamate vietate)

NOTA SULLA LETTURA DEL YAML
---------------------------
Il YAML può contenere chiavi esplicitamente vuote:

    validation:
      # solo commenti

In quel caso `yaml.safe_load()` produce `{'validation': None}` — la chiave
ESISTE, il valore è `None`. Il default di `dict.get(key, default)` viene usato
SOLO se la chiave è assente, non se il valore è `None`. Per questo TUTTE le
letture di sezioni che possono essere vuote usano il pattern:

    value = config.get("chiave") or {}

oppure:

    value = config.get("chiave") or []

che è l'unico modo robusto di trattare YAML con sezioni vuote. Il bug
`'NoneType' object has no attribute 'get'` era causato proprio da questo.
"""

from __future__ import annotations

import re
from typing import Any

# Azioni che eseguono SQL arbitrario (scrittura). Per queste vale solo la lista
# `always_deny_sql`; INSERT/UPDATE/DELETE sono ammesse (con approvazione umana).
_SQL_WRITE_ACTIONS = {"sql.execute_write", "sql.execute_write_for_connection"}

# Verbi che, per convenzione di naming del progetto (<dominio>.<verbo>_...),
# indicano in modo INEQUIVOCABILE un'azione di sola lettura. Usati SOLO come
# fallback per le azioni che non compaiono né in `allowed_actions` né in
# `write_actions` nella policy YAML — cioè un tool nuovo, dimenticato nello YAML.
# Questo evita la classe di bug già vista più volte in questo progetto: un tool di
# lettura non ancora aggiunto manualmente alla policy finiva trattato come
# scrittura (fail-closed) e chiedeva approvazione senza motivo.
#
# IMPORTANTE — non è una whitelist che scavalca la policy: se un'azione è
# ESPLICITAMENTE elencata in `write_actions`, resta scrittura con approvazione
# SEMPRE, indipendentemente dal suo nome (mai un bypass silenzioso per un'azione
# dichiarata pericolosa). La convenzione qui sotto è deliberatamente conservativa
# (solo prefissi inequivocabili): in caso di dubbio il fallback resta "scrittura",
# cioè il comportamento sicuro di oggi.
_READ_VERB_PREFIXES = (
    "get_", "list_", "read_", "show_", "search_", "check_", "analyze_",
    "describe_", "find_", "fetch_", "resolve_", "preview_",
)


def _is_read_by_convention(action: str) -> bool:
    """True solo se il verbo del tool è inequivocabilmente di lettura per nome."""
    verb = action.split(".", 1)[1] if "." in action else action
    return verb.startswith(_READ_VERB_PREFIXES)


def _as_list(value: Any) -> list:
    """Coercizione difensiva: qualunque cosa non-lista diventa lista vuota.

    Serve per i campi YAML che possono essere esplicitamente vuoti (`null`).
    Un default nel `.get()` non basta: il default scatta solo su chiave assente,
    non su chiave presente con valore `None`.
    """
    if isinstance(value, list):
        return value
    return []


def _as_dict(value: Any) -> dict:
    """Coercizione difensiva: qualunque cosa non-dict diventa dict vuoto."""
    if isinstance(value, dict):
        return value
    return {}


def _as_int(value: Any, default: int) -> int:
    """Coercizione difensiva a intero, con fallback al default se None/non numerico."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class GuardrailViolation(Exception):
    """Sollevata quando un'azione viola le policy di sicurezza."""


class Guardrails:
    def __init__(
        self,
        policy: dict[str, Any],
        previews: dict[str, Any] | None = None,
        approval_fn: Any = None,
        native_tool_names: set[str] | None = None,
    ) -> None:
        # `policy` deve essere un dict: se per errore arriva None (YAML vuoto
        # senza nemmeno un commento), degradiamo a dict vuoto con policy
        # fail-closed (tutto ciò che non è in `allowed_actions` richiede
        # approvazione). Il codice a valle non deve mai crashare su questo.
        self.policy = policy if isinstance(policy, dict) else {}

        self.allowed = set(_as_list(self.policy.get("allowed_actions")))

        # --- Sezione write_actions (può essere nulla nel YAML) ---------------
        write_cfg = _as_dict(self.policy.get("write_actions"))
        # Azioni di scrittura note: consentite MA solo con approvazione umana.
        self.write_actions = set(
            _as_list(write_cfg.get("actions", write_cfg.get("denied_by_default")))
        )
        self.require_approval = bool(write_cfg.get("require_human_approval", True))

        # Provider di anteprima per il gate di approvazione: action -> fn(args)->str.
        self.previews = previews or {}
        # Callback di approvazione iniettabile: fn(action, args, preview) -> bool.
        # Se None si usa il prompt CLI (input()). La UI web inietta un callback
        # basato su eventi/queue, così lo stesso guardrail serve CLI e web.
        self.approval_fn = approval_fn

        # --- Policy su domini, tool nativi e plugin -------------------------
        # Insieme dei nomi dei tool nativi (es. {"github.get_file", "adf.create_pipeline"}).
        # Iniettato dal chiamante (web.py / cli.py) che conosce il catalogo.
        # Se non fornito, il controllo su native_tools_policy viene saltato
        # (fail-open sul solo controllo di collisione; tutti gli altri restano).
        self.native_tool_names: set[str] = set(native_tool_names or [])

        # Domini riservati (es. {"filesystem", "shell", "os"}).
        # YAML può essere `reserved_domains:` con lista vuota o assente → degrada a [].
        self.reserved_domains: set[str] = set(
            _as_list(self.policy.get("reserved_domains"))
        )

        # --- Policy sui tool nativi (sezione opzionale, può essere vuota) ----
        nt_cfg = _as_dict(self.policy.get("native_tools_policy"))
        self.native_immutable = bool(nt_cfg.get("immutable", True))
        self.native_forbidden_ops = set(_as_list(nt_cfg.get("forbidden_operations")))

        # --- Policy sui plugin utente (sezione opzionale, può essere vuota) --
        up_cfg = _as_dict(self.policy.get("user_plugins_policy"))
        self.plugin_owned_by_user = bool(up_cfg.get("owned_by_user", True))
        self.plugin_forbid_native_collision = bool(
            up_cfg.get("forbid_native_collision", True)
        )
        self.plugin_max_per_user = _as_int(up_cfg.get("max_plugins_per_user"), 50)
        self.plugin_max_size_bytes = _as_int(
            up_cfg.get("max_plugin_size_bytes"), 50000
        )

        # --- Validazione statica del codice dei plugin -----------------------
        cv = _as_dict(self.policy.get("plugin_code_validation"))
        self.plugin_code_validation_enabled = bool(cv.get("enabled", True))
        self.forbidden_imports = _as_list(cv.get("forbidden_imports"))
        self.forbidden_calls = _as_list(cv.get("forbidden_calls"))
        self.forbidden_plugin_name_prefixes = tuple(
            _as_list(cv.get("forbidden_plugin_name_prefixes"))
        )

    # =====================================================================
    # Classificazione azioni (invariata)
    # =====================================================================

    def is_write_action(self, action: str) -> bool:
        """Determina se un'azione è di scrittura (richiede approvazione umana).

        Ordine di decisione (la policy YAML resta sempre autoritativa quando si
        esprime esplicitamente):
        1. Azione in `write_actions` -> scrittura, SEMPRE (mai bypassabile per nome).
        2. Azione in `allowed_actions` -> lettura, SEMPRE (già dichiarata sicura).
        3. Azione non classificata in nessuna delle due liste -> fallback per
           convenzione di naming: se il verbo è inequivocabilmente di lettura
           (get_/list_/read_/...) viene trattata come lettura senza approvazione;
           altrimenti resta scrittura (comportamento fail-closed di prima).
        """
        if action in self.write_actions:
            return True
        if action in self.allowed:
            return False
        return not _is_read_by_convention(action)

    def check(self, action: str, args: dict[str, Any]) -> None:
        """Valida un'azione prima dell'esecuzione. Solleva GuardrailViolation se vietata.

        - Azioni read-only note -> consentite senza approvazione.
        - Azioni di scrittura -> validazione + approvazione umana obbligatoria.
        - Azioni in domini riservati -> SEMPRE rifiutate.
        """
        # GATE 0: dominio riservato -> stop immediato, qualunque sia la policy.
        # Questo copre anche il caso in cui, per errore di configurazione, un
        # tool riservato fosse finito in allowed_actions: la reserved list vince.
        if self.is_reserved_action(action):
            raise GuardrailViolation(
                f"Azione '{action}' appartiene a un dominio riservato "
                f"(non disponibile agli utenti)."
            )

        if action in self.allowed:
            self._validate_args(action, args)
            return

        if self.is_write_action(action):
            self._validate_args(action, args)
            preview = None
            provider = self.previews.get(action)
            if provider:
                try:
                    preview = provider(args)
                except Exception as exc:  # noqa: BLE001 - anteprima non deve bloccare
                    preview = f"(anteprima non disponibile: {exc})"
            approve = self.approval_fn or self._request_human_approval
            if self.require_approval and not approve(action, args, preview):
                raise GuardrailViolation(
                    f"Azione di scrittura '{action}' non approvata dall'operatore."
                )
            return

        self._validate_args(action, args)

    # =====================================================================
    # Domini riservati
    # =====================================================================

    def _domain_of(self, action: str) -> str:
        """Estrae il dominio da un nome azione `dominio.verbo`."""
        return action.split(".", 1)[0] if "." in action else action

    def is_reserved_domain(self, domain: str) -> bool:
        """True se il dominio è nella lista `reserved_domains`."""
        return domain in self.reserved_domains

    def is_reserved_action(self, action: str) -> bool:
        """True se l'azione appartiene a un dominio riservato."""
        return self.is_reserved_domain(self._domain_of(action))

    def filter_catalog(self, action_names: list[str]) -> list[str]:
        """Ritorna solo le azioni non riservate.

        Usato per filtrare il catalogo dei tool PRIMA di passarlo all'agente,
        così i domini riservati non sono nemmeno visibili al modello.
        """
        return [a for a in action_names if not self.is_reserved_action(a)]

    # =====================================================================
    # Tool nativi
    # =====================================================================

    def is_native_tool(self, name: str) -> bool:
        """True se `name` è un tool nativo registrato dall'agente."""
        return name in self.native_tool_names

    def can_modify_tool(self, name: str) -> bool:
        """True se l'utente può modificare/rimuovere il tool `name`.

        - Tool nativo: NO (immutabile).
        - Plugin utente: sì (a meno che la policy non lo vieti esplicitamente).
        """
        if self.is_native_tool(name) and self.native_immutable:
            return False
        return self.plugin_owned_by_user

    def can_shadow_tool(self, name: str) -> bool:
        """True se l'utente può creare un plugin con lo stesso nome `name`.

        Default: no, se il nome collidesce con un tool nativo e
        `forbid_native_collision` è attivo.
        """
        if self.plugin_forbid_native_collision and self.is_native_tool(name):
            return False
        return True

    # =====================================================================
    # Plugin utente: validazione della proposta
    # =====================================================================

    def validate_plugin_proposal(
        self,
        name: str,
        code: str,
        current_user_plugin_count: int | None = None,
    ) -> list[str]:
        """Valida un plugin proposto dall'utente.

        Ritorna una lista di errori (vuota = ok). Non solleva eccezioni, così
        `meta.propose_tool` può mostrare all'utente tutti i problemi insieme
        invece di fermarsi al primo.

        Controlli eseguiti:
          1. Nome ben formato (`dominio.azione`, no spazi, no path).
          2. Dominio non riservato.
          3. Nome non in conflitto con tool nativi (shadowing vietato).
          4. Prefissi di nome vietati (plugin_code_validation.forbidden_plugin_name_prefixes).
          5. Limite numero plugin per utente.
          6. Limite dimensione codice.
          7. Import vietati nel codice.
          8. Chiamate vietate nel codice.
        """
        errors: list[str] = []
        name = (name or "").strip()
        code = code or ""

        # 1. Formato del nome
        if not name:
            errors.append("Il nome del plugin è obbligatorio.")
        elif not re.match(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$", name):
            errors.append(
                f"Nome plugin non valido: '{name}'. "
                "Formato richiesto: dominio.azione (minuscole, underscore, es. 'github.manage')."
            )
        else:
            domain = self._domain_of(name)

            # 2. Dominio riservato
            if self.is_reserved_domain(domain):
                errors.append(
                    f"Dominio '{domain}' riservato: gli utenti non possono creare "
                    "plugin in questo dominio."
                )

            # 3. Collisione con tool nativo (shadowing)
            if self.plugin_forbid_native_collision and self.is_native_tool(name):
                errors.append(
                    f"Esiste già un tool nativo '{name}'. "
                    "Non è possibile sovrascrivere un tool nativo con un plugin."
                )

            # 4. Prefissi vietati
            for pref in self.forbidden_plugin_name_prefixes:
                if name.startswith(pref):
                    errors.append(
                        f"Nome plugin '{name}' vietato: inizia con un prefisso "
                        f"riservato ('{pref}')."
                    )
                    break

        # 5. Limite numero plugin
        if (
            current_user_plugin_count is not None
            and current_user_plugin_count >= self.plugin_max_per_user
        ):
            errors.append(
                f"Limite plugin raggiunto ({self.plugin_max_per_user}). "
                "Rimuovi un plugin prima di crearne uno nuovo."
            )

        # 6. Limite dimensione
        if (
            self.plugin_max_size_bytes
            and len(code.encode("utf-8")) > self.plugin_max_size_bytes
        ):
            errors.append(
                f"Codice plugin troppo grande "
                f"({len(code.encode('utf-8'))} byte, max {self.plugin_max_size_bytes})."
            )

        # 7-8. Scansione statica del codice
        if self.plugin_code_validation_enabled:
            errors.extend(self._scan_plugin_code(code))

        return errors

    def _scan_plugin_code(self, code: str) -> list[str]:
        """Scansione statica del codice del plugin.

        Non è una sandbox (Python non è sandboxabile in-process), ma blocca i
        casi ovvi e alza il costo di un bypass. L'installazione richiede
        comunque approvazione umana.
        """
        errors: list[str] = []

        for mod in self.forbidden_imports:
            # Match `import mod`, `import mod as x`, `from mod import ...`,
            # `from mod.sub import ...`.
            pattern = rf"^\s*(?:import\s+{re.escape(mod)}\b|from\s+{re.escape(mod)}(?:\.|\s))"
            if re.search(pattern, code, re.MULTILINE):
                errors.append(f"Import vietato: '{mod}'.")

        for call in self.forbidden_calls:
            if call in code:
                errors.append(f"Chiamata/pattern vietato: '{call}'.")

        return errors

    # =====================================================================
    # Validazione argomenti
    # =====================================================================

    def _validate_args(self, action: str, args: dict[str, Any]) -> None:
        """Validazione degli argomenti.

        - always_deny_sql: keyword SQL vietate in modo assoluto (anche con approvazione),
          applicate solo all'azione dedicata di scrittura `sql.execute_write`.
        - block_destructive_sql: blocca keyword pericolose passate ai tool di LETTURA
          (previene SQL injection nei percorsi read-only).

        NOTA: `validation` è letta con `or {}` perché un YAML con la sola
        intestazione `validation:` (seguita da commenti) produce
        `{'validation': None}`. Il default di `.get("validation", {})` NON basta:
        scatta solo su chiave assente, non su valore None. Con `or {}` il
        comportamento è robusto in entrambi i casi.
        """
        validation = self.policy.get("validation") or {}
        if not isinstance(validation, dict):
            # YAML malformato (es. `validation: "stringa"`): degrada a dict vuoto.
            validation = {}

        # Azioni di scrittura SQL (locale o su connessione esterna): sono ammesse le
        # operazioni DML/DDL normali (INSERT/UPDATE/DELETE/CREATE/ALTER); solo le
        # keyword in `always_deny_sql` (es. DROP/TRUNCATE) restano vietate.
        if action in _SQL_WRITE_ACTIONS:
            raw = str(args.get("query", ""))
            for kw in validation.get("always_deny_sql") or []:
                if re.search(rf"\b{re.escape(kw)}\b", raw, re.IGNORECASE):
                    raise GuardrailViolation(
                        f"Comando SQL '{kw}' vietato dalla policy (anche con approvazione)."
                    )
            return

        # Anti-injection: solo per eventuali tool NON di scrittura che ricevano una
        # query grezza. Match su confini di parola (evita falsi positivi come
        # 'id_insert'). I normali tool di lettura non hanno un argomento 'query'.
        if (
            validation.get("block_destructive_sql")
            and action.startswith("sql.")
            and "query" in args
        ):
            raw = str(args.get("query", ""))
            for kw in ("DROP", "DELETE", "TRUNCATE", "UPDATE", "INSERT"):
                if re.search(rf"\b{kw}\b", raw, re.IGNORECASE):
                    raise GuardrailViolation(f"Query SQL distruttiva bloccata ({kw}).")

    def _request_human_approval(
        self, action: str, args: dict[str, Any], preview: str | None = None
    ) -> bool:
        """Richiede conferma umana esplicita via CLI.

        Mostra azione, argomenti, eventuale anteprima delle modifiche e impatto.
        Ritorna True solo se l'operatore risponde 'sì'/'si'/'y'/'yes'.
        """
        print("\n" + "=" * 60)
        print("APPROVAZIONE RICHIESTA - azione di scrittura")
        print(f"  Tool : {action}")
        print(f"  Args : {args}")
        if preview:
            print("-" * 60)
            print("Anteprima delle modifiche:")
            print(preview)
        print("  Impatto: questa azione MODIFICA una risorsa Azure.")
        print("=" * 60)
        try:
            answer = input("Confermi l'esecuzione? (si/no): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in {"si", "sì", "s", "y", "yes"}