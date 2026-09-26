"""Meta-tooling: l'agente estende sé stesso con nuovi tool, SEMPRE fuori dal
proprio codice sorgente e sempre con approvazione umana.

Flusso previsto:
  1. `meta.analyze_request` — dato il task, cerca un tool esistente che lo copra o una
     COMBINAZIONE di tool esistenti; se non basta, segnala che serve un nuovo tool.
  2. `meta.propose_tool` — valida (senza scrivere nulla) il codice del nuovo tool
     generato dall'AI: nome, sintassi, entrypoint `run`, pattern vietati. Ritorna
     un esito sintetico (valid/errors/warnings) SENZA path e SENZA anteprima del
     codice, per non far trapelare dettagli interni al modello.
  3. `meta.install_tool` — [WRITE, approvazione] scrive il plugin nell'area RUNTIME
     isolata (`data/plugins/`, MAI dentro `src/hemmy/`), verifica che importi,
     e segnala che il catalogo va ricaricato. L'anteprima integrale del codice è
     mostrata all'utente tramite il canale di approvazione, non al modello.
  4. Il sistema ricarica i tool A CALDO, in-process, subito dopo il turno corrente:
     NESSUN riavvio del processo/servizio è necessario.

Design di sicurezza a più livelli (nessuno dei quali da solo è sufficiente):
  - Il codice del tool è generato dal modello ma NON viene mai eseguito finché
    l'operatore non approva `meta.install_tool` (gate umano + preview integrale
    mostrata dalla UI, non passata al modello).
  - `_validate_code` BLOCCA (non solo segnala) i pattern che permetterebbero a un
    tool di eseguire codice arbitrario, cancellare/sovrascrivere file, o toccare il
    codice sorgente dell'agente (`os.system`, `eval`/`exec`, scrittura/cancellazione
    di file, riferimenti a `src/hemmy`/`native_plugins`) — la sola revisione
    umana si è già dimostrata insufficiente in pratica per coglierli tutti.
  - `_validate_code` BLOCCA anche la lettura di credenziali da variabile d'ambiente
    (`os.environ`/`os.getenv` per token/password/API key/secret): un tool generato
    deve SEMPRE leggere le credenziali per-utente configurate nelle Impostazioni
    tramite `auth.user_context.get_current_user_secret()`, mai un env var condiviso
    da tutto il processo.
  - I tool generati vivono FISICAMENTE fuori dal sorgente (`data/plugins/`, mai
    `src/hemmy/`): anche un futuro bug di path handling non potrebbe far
    atterrare un plugin accanto al codice reale (vedi `plugins/__init__.py`).
  - `base_dir`/`owner_user_id`/`guardrails` non sono mai argomenti accettati dal
    modello (rimossi da ogni Action Input in `core/agent.py._RESERVED_ARG_NAMES`):
    arrivano SOLO da `functools.partial` legato in `cli.py` / `web.py`.

Robustezza del framework (IMPORTANTE):
  - Alcuni runtime di tool catturano le eccezioni sollevate dai tool e le sostituiscono
    con `None`, per poi chiamare `.get()` su quel `None` → errore opaco
    `'NoneType' object has no attribute 'get'` che nasconde la vera causa.
  - Per questo `propose_tool` NON solleva MAI eccezioni: qualunque errore inatteso
    viene catturato, loggato su stderr per diagnosi server-side, e ritornato nel
    campo `errors` del dict di risposta. Stessa filosofia applicata a `install_tool`
    e `remove_plugin` (che però continuano a segnalare gli errori di validazione in
    modo esplicito, senza mai propagare eccezioni al framework).

Policy di dominio, tool nativi e plugin utente (integrata con `Guardrails`):
  - Domini riservati (`filesystem`, `shell`, `os`, `sys`, `subprocess`, …): mai
    utilizzabili in un plugin generato. Se `guardrails` è iniettato, la lista è
    letta da `config/guardrails.yaml` (`reserved_domains`); altrimenti si applica
    un fallback conservativo hardcoded in questo modulo.
  - Tool nativi (`github.get_file`, `adf.create_pipeline`, …): l'utente può USARLI
    e LEGGERLI, ma non può mai creare un plugin con lo stesso nome (shadowing),
    né cancellarli via `meta.remove_plugin`. Se `guardrails` è iniettato, il
    controllo usa `guardrails.is_native_tool()` / `guardrails.can_modify_tool()`.
  - Plugin utente: liberamente creabili/leggibili/modificabili/cancellabili,
    soggetti solo ai vincoli sopra.
"""

from __future__ import annotations

import ast
import re
import sys
import traceback
from pathlib import Path
from typing import Any

from hemmy.plugins import (
    delete_plugin_from_db,
    is_inside_runtime_plugins_area,
    is_valid_tool_name,
    load_plugins,
    plugins_dir,
    save_plugin_to_db,
)

# Pattern BLOCCANTI: un tool generato che li contiene NON è installabile, nemmeno
# con approvazione umana (finiscono in `errors`, non in `warnings`). Sono
# esattamente la classe di codice che permetterebbe a un tool "per l'utente" di
# modificare il codice sorgente dell'agente stesso o compiere danni irreversibili
# sul filesystem — la revisione umana da sola si è già dimostrata insufficiente
# in questo progetto (un tool `filesystem.write`/`refactor.*` di questo tipo è
# stato effettivamente approvato ed eseguito prima di questo hardening). Nessun
# tool generato dal modello può contenere questi pattern: sono riservati SOLO
# alla baseline nativa scritta e revisionata dagli sviluppatori (`native_plugins/`).
_BLOCKING_PATTERNS = [
    (re.compile(r"\bos\.system\s*\("), "os.system() — esecuzione shell arbitraria"),
    (re.compile(r"subprocess\.[A-Za-z_]+\("), "subprocess.* — esecuzione di processi esterni"),
    (re.compile(r"\beval\s*\("), "eval() — esecuzione di codice arbitrario"),
    (re.compile(r"\bexec\s*\("), "exec() — esecuzione di codice arbitrario"),
    (re.compile(r"\b__import__\s*\("), "__import__() — import dinamico non tracciabile"),
    (re.compile(r"\bopen\s*\([^)]*['\"][wa]"), "apertura file in scrittura/append — un tool non deve mai scrivere file arbitrari"),
    (re.compile(r"\b(os\.remove|os\.unlink|os\.rmdir|shutil\.rmtree)\s*\("), "cancellazione di file/cartelle"),
    (re.compile(r"\bos\.rename\s*\(|\bos\.replace\s*\("), "rinomina/sovrascrittura di file"),
    (re.compile(r"\bos\.chmod\s*\(|\bos\.chown\s*\("), "modifica permessi/proprietario di file"),
    (re.compile(r"src[/\\]hemmy|native_plugins"), "riferimento al codice sorgente dell'agente (mai consentito in un tool generato)"),
    (re.compile(r"__file__"), "introspezione del proprio percorso sorgente (tipico primo passo per auto-modificarsi)"),
    (
        re.compile(
            r"os\.(?:environ(?:\.get)?|getenv)\s*\(\s*['\"][^'\"]*"
            r"(?:token|secret|password|passwd|pwd|api[_-]?key|apikey|credential|client[_-]?secret|auth)",
            re.IGNORECASE,
        ),
        "lettura di una credenziale da variabile d'ambiente — VIETATO: ogni utente ha le "
        "proprie credenziali configurate nelle Impostazioni (cifrate, per-utente). Un tool "
        "che legge `os.environ`/`os.getenv` per token/password/API key userebbe la STESSA "
        "credenziale condivisa per tutti gli utenti della piattaforma. Usa invece "
        "`from hemmy.auth.user_context import get_current_user_secret` per leggere il "
        "segreto dell'utente corrente.",
    ),
]

# Pattern che meritano solo un warning nella preview (non bloccano: decide l'umano
# in fase di approvazione, es. chiamate di rete verso host esterni).
_RISKY = [
    (re.compile(r"\brequests\.[a-z]+\(|urllib\.request\."), "chiamata di rete verso un host esterno"),
]

# Fallback conservativo se `guardrails` non è iniettato: gli stessi domini
# riservati definiti in `config/guardrails.yaml`. Vengono applicati sempre,
# anche in assenza dell'oggetto Guardrails, così un plugin non può mai
# chiamarsi `filesystem.*` o `shell.*` per errore di binding.
_FALLBACK_RESERVED_DOMAINS = frozenset({
    "filesystem", "shell", "subprocess", "process", "system",
    "exec", "eval", "import", "os", "sys", "socket", "ssh",
    "docker", "kubernetes", "admin", "internal",
})

_STOPWORDS = {
    "il", "lo", "la", "i", "gli", "le", "un", "una", "di", "a", "da", "in", "con", "su",
    "per", "tra", "fra", "e", "che", "del", "della", "dei", "delle", "al", "the", "of",
    "to", "and", "vuole", "voglio", "sistema", "tool", "agente", "fare", "creare",
}


# --------------------------------------------------------------------------- utils


def _as_str(v: Any) -> str:
    """Coercizione difensiva a stringa.

    Il modello può passare `None`, numeri, liste o dict al posto di stringhe
    (specie quando il framework non è rigoroso nella validazione dello schema
    di Action Input). Meglio degradare a stringa vuota o a `str(v)` che
    sollevare un `TypeError` a valle — un `TypeError` catturato dal framework
    diventerebbe spesso un opaco `'NoneType' object has no attribute 'get'`.
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return str(v)
    except Exception:  # noqa: BLE001
        return ""


def _log_internal_error(context: str, exc: BaseException) -> None:
    """Logga su stderr un errore inatteso dentro i meta-tool.

    Il framework dell'agente cattura le eccezioni dei tool e le trasforma in
    osservazioni opache. Per non perdere la causa reale, marchiamo su stderr
    sia il contesto sia il traceback completo. Utile in diagnosi server-side
    (`HEMMY_LOG_LEVEL=DEBUG`).
    """
    try:
        print(f"[meta-tooling] errore interno in {context}: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in _STOPWORDS}


def list_capabilities(tool_docs: dict[str, str]) -> dict[str, Any]:
    """Inventario dei tool attuali, raggruppati per dominio (per ragionare sulla copertura)."""
    groups: dict[str, list[dict[str, str]]] = {}
    for name in sorted(tool_docs):
        domain = name.split(".", 1)[0]
        groups.setdefault(domain, []).append({"name": name, "doc": tool_docs[name]})
    plugins = [p for p in load_plugins() if "error" not in p]
    return {
        "total": len(tool_docs),
        "domains": {d: len(v) for d, v in groups.items()},
        "tools": groups,
        "plugins_installed": [p["name"] for p in plugins],
    }


def analyze_request(tool_docs: dict[str, str], description: str, keywords: list[str] | None = None) -> dict[str, Any]:
    """Valuta se il task è coperto da un tool esistente, da una combinazione, o serve nuovo.

    Scorer deterministico su overlap di parole chiave tra la richiesta e le doc dei tool.
    Il verdetto è un SUGGERIMENTO: l'agente decide come procedere.
    """
    want = _tokens(_as_str(description))
    for k in keywords or []:
        want |= _tokens(_as_str(k))
    scored = []
    for name, doc in tool_docs.items():
        have = _tokens(name.replace(".", " ")) | _tokens(_as_str(doc))
        overlap = want & have
        if overlap:
            scored.append((len(overlap), name, sorted(overlap)))
    scored.sort(reverse=True)
    top = [{"name": n, "match": m, "score": s} for s, n, m in scored[:8]]

    best = scored[0][0] if scored else 0
    if best >= 3:
        verdict = "covered"
        rationale = f"Esiste un tool con forte corrispondenza ({top[0]['name']}). Valuta se copre già il task."
    elif best >= 1:
        verdict = "combine"
        rationale = "Nessun tool copre l'intero task, ma alcuni sono affini: valuta una combinazione prima di crearne uno nuovo."
    else:
        verdict = "needs_new_tool"
        rationale = "Nessun tool esistente è pertinente: probabile necessità di un nuovo tool (usa meta.propose_tool)."
    return {"verdict": verdict, "rationale": rationale, "candidates": top, "keywords": sorted(want)}


# ===========================================================================
# Validazione di un plugin proposto (dominio, collisione con nativi, codice)
# ===========================================================================


def _domain_of(name: str) -> str:
    return name.split(".", 1)[0] if "." in name else name


def _policy_errors(
    name: str,
    guardrails: Any = None,
) -> list[str]:
    """Errori di policy indipendenti dal codice: dominio riservato, collisione
    con tool nativi. Funziona con o senza `guardrails` iniettato.

    - Se `guardrails` è presente, la lista `reserved_domains` e l'insieme
      `native_tool_names` sono letti dall'oggetto (che a sua volta li legge da
      `config/guardrails.yaml` + catalogo dell'agente).
    - Se assente, si applica un fallback hardcoded conservativo per i domini
      riservati; il controllo sui tool nativi viene saltato (nessuna fonte
      affidabile per conoscerli).
    """
    errors: list[str] = []
    name = _as_str(name).strip()
    if not name:
        return errors

    domain = _domain_of(name)

    # Dominio riservato
    if guardrails is not None:
        try:
            is_reserved = bool(guardrails.is_reserved_domain(domain))
        except Exception:  # noqa: BLE001
            is_reserved = domain in _FALLBACK_RESERVED_DOMAINS
    else:
        is_reserved = domain in _FALLBACK_RESERVED_DOMAINS

    if is_reserved:
        errors.append(
            f"Dominio '{domain}' riservato: gli utenti non possono creare plugin "
            "in questo dominio."
        )

    # Collisione con un tool nativo (shadowing)
    if guardrails is not None:
        try:
            if guardrails.is_native_tool(name):
                errors.append(
                    f"Esiste già un tool nativo '{name}'. Non è possibile "
                    "sovrascrivere un tool nativo con un plugin."
                )
            elif not guardrails.can_shadow_tool(name):
                errors.append(
                    f"Nome '{name}' non consentito: entra in conflitto con un "
                    "tool nativo."
                )
        except Exception:  # noqa: BLE001
            # `guardrails` iniettato ma con API non compatibile: non blocchiamo
            # la validazione, il fallback sopra copre già i domini riservati.
            pass

    return errors


def _validate_code(name: str, code: str, guardrails: Any = None) -> dict[str, Any]:
    """Validazione di un plugin proposto. Ritorna {errors, warnings}.

    Ordine di esecuzione:
      1. Nome ben formato (via `is_valid_tool_name` di plugins/__init__.py).
      2. Policy di dominio/collisione (via `_policy_errors`, che usa `guardrails`
         se iniettato, altrimenti fallback hardcoded).
      3. Sintassi Python valida + entry point `run(**kwargs)`.
      4. Pattern bloccanti (`_BLOCKING_PATTERNS`): codice arbitrario, filesystem,
         credenziali da env var, ecc.
      5. Pattern rischiosi (`_RISKY`): warning non bloccanti.

    Non solleva MAI eccezioni: qualunque errore inatteso viene catturato e
    riportato come errore di validazione. Questo evita che un `TypeError` su
    input non-stringa diventi, a valle, un opaco
    `'NoneType' object has no attribute 'get'` del framework.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Coercizione difensiva: input non-stringa non devono far crashare la validazione
    name = _as_str(name)
    code = _as_str(code)

    # 1. Nome
    try:
        if not is_valid_tool_name(name):
            errors.append("nome non valido: atteso 'domain.action' (minuscole/underscore).")
    except Exception as exc:  # noqa: BLE001
        _log_internal_error("is_valid_tool_name", exc)
        errors.append(f"errore nella validazione del nome: {type(exc).__name__}: {exc}")

    # 2. Policy (dominio riservato, collisione nativi) — sempre eseguita
    try:
        errors.extend(_policy_errors(name, guardrails))
    except Exception as exc:  # noqa: BLE001
        _log_internal_error("_policy_errors", exc)
        errors.append(f"errore nella validazione della policy: {type(exc).__name__}: {exc}")

    # 3. Sintassi + entry point
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        errors.append(f"sintassi Python non valida: {exc}")
        return {"errors": errors, "warnings": warnings}
    except Exception as exc:  # noqa: BLE001
        _log_internal_error("ast.parse", exc)
        errors.append(f"errore nel parsing del codice: {type(exc).__name__}: {exc}")
        return {"errors": errors, "warnings": warnings}

    try:
        has_run = any(
            isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body
        )
        if not has_run:
            errors.append("il codice deve definire una funzione top-level `def run(**kwargs)`.")
    except Exception as exc:  # noqa: BLE001
        _log_internal_error("check run()", exc)
        errors.append(f"errore nella ricerca di run(): {type(exc).__name__}: {exc}")

    # 4. Pattern bloccanti
    try:
        for pat, label in _BLOCKING_PATTERNS:
            if pat.search(code):
                errors.append(f"operazione vietata in un tool generato: {label}")
    except Exception as exc:  # noqa: BLE001
        _log_internal_error("_BLOCKING_PATTERNS scan", exc)
        errors.append(f"errore nella scansione dei pattern bloccanti: {type(exc).__name__}: {exc}")

    # 5. Pattern rischiosi (warning)
    try:
        for pat, label in _RISKY:
            if pat.search(code):
                warnings.append(f"operazione potenzialmente sensibile: {label}")
    except Exception as exc:  # noqa: BLE001
        _log_internal_error("_RISKY scan", exc)
        warnings.append(f"errore nella scansione dei pattern rischiosi: {type(exc).__name__}: {exc}")

    return {"errors": errors, "warnings": warnings}


def _plugin_source(name: str, doc: str, code: str, write: bool) -> str:
    esc_doc = _as_str(doc).replace('"', '\\"')
    header = f"# Plugin auto-generato per il tool '{name}'.\n# Installato via meta.install_tool con approvazione umana.\n\n"
    manifest = (
        "\nMANIFEST = {\n"
        '    "tools": [\n'
        f'        {{"name": "{name}", "doc": "{esc_doc}", "write": {bool(write)}, "entrypoint": "run"}}\n'
        "    ]\n"
        "}\n"
    )
    return header + _as_str(code).rstrip() + "\n" + manifest


def _module_stem(name: str) -> str:
    return _as_str(name).replace(".", "__")


def propose_tool(
    name: str = "",
    doc: str = "",
    code: str = "",
    write: bool = False,
    ui: str = "",
    guardrails: Any = None,
) -> dict[str, Any]:
    """Valida un nuovo tool SENZA scrivere nulla.

    Ritorna un esito SINTETICO adatto ad essere passato al modello come
    Observation: `name`, `doc`, `write`, `valid`, `errors`, `warnings`,
    `ui_change`. NON ritorna:
      - `target_file`: rivelerebbe la struttura interna di `data/plugins/`;
      - `preview`: contiene il codice completo del plugin, che il modello non
        deve ricevere in eco (lo vede già, è lui ad averlo scritto).

    L'anteprima integrale viene mostrata all'utente nel momento in cui approva
    `meta.install_tool`, tramite il canale di approvazione (`approval_fn`), che
    è separato dal canale Observation. Questo chiude il buco di information
    disclosure: se un utente chiede "dove finirà il file?" o "mostrami il
    codice", l'osservazione del tool non contiene né il path né il sorgente.

    ROBUSTEZZA: la funzione non solleva MAI eccezioni. Qualunque errore inatteso
    viene catturato, loggato su stderr per diagnosi, e riportato nel campo
    `errors` del dict di risposta. Motivo: alcuni runtime di tool catturano le
    eccezioni dei tool e le sostituiscono con `None`, generando a valle errori
    opachi tipo `'NoneType' object has no attribute 'get'` che nascondono la
    vera causa. Ritornare sempre un dict strutturato rende il tool robusto a
    qualunque input, anche malformato.
    """
    # --- Coercizione difensiva degli input --------------------------------
    # Il modello può passare `None`, numeri, liste o dict al posto di stringhe.
    # Meglio degradare a stringa che sollevare TypeError a valle.
    try:
        name_s = _as_str(name)
        doc_s = _as_str(doc)
        code_s = _as_str(code)
        ui_s = _as_str(ui)
    except Exception as exc:  # noqa: BLE001
        _log_internal_error("propose_tool::coercion", exc)
        return {
            "name": "",
            "doc": "",
            "write": False,
            "valid": False,
            "errors": [f"errore nella coercizione degli input: {type(exc).__name__}: {exc}"],
            "warnings": [],
            "ui_change": None,
            "note": "Errore interno durante la preparazione degli input.",
        }

    try:
        write_b = bool(write)
    except Exception:  # noqa: BLE001
        write_b = False

    # --- Validazione (mai solleva: _validate_code cattura internamente) ----
    try:
        v = _validate_code(name_s, code_s, guardrails=guardrails)
        if not isinstance(v, dict):
            v = {"errors": ["_validate_code ha restituito un valore inatteso"], "warnings": []}
    except Exception as exc:  # noqa: BLE001
        # Rete di sicurezza finale: non dovrebbe mai accadere, ma se accade
        # vogliamo comunque ritornare un dict valido al framework.
        _log_internal_error("propose_tool::_validate_code", exc)
        v = {
            "errors": [f"errore interno durante la validazione: {type(exc).__name__}: {exc}"],
            "warnings": [],
        }

    errors = list(v.get("errors") or [])
    warnings = list(v.get("warnings") or [])
    valid = not errors

    return {
        "name": name_s,
        "doc": doc_s,
        "write": write_b,
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "ui_change": ui_s or None,
        "note": (
            "Se approvato, il tool verrà scritto nell'area plugin runtime "
            "(fuori dal codice sorgente dell'agente) e ricaricato a caldo. "
            "Nessun codice viene eseguito prima dell'approvazione umana."
        ),
    }


def _resolve_base(base_dir: Any) -> Path:
    """Cartella target dei plugin: quella per-utente se indicata, altrimenti il
    runtime globale. SICUREZZA: `base_dir` non è mai un argomento del modello
    (`core/agent.py._RESERVED_ARG_NAMES` lo rimuove da ogni Action Input prima
    della chiamata) — arriva SOLO da `functools.partial` legato in `cli.py`. Qui
    aggiungiamo comunque un controllo di contenimento come seconda linea di
    difesa: qualunque cosa arrivi, il risultato deve stare dentro l'area plugin
    RUNTIME (`data/plugins/`) e MAI dentro il codice sorgente dell'applicazione."""
    base = Path(base_dir) if base_dir else plugins_dir()
    if not is_inside_runtime_plugins_area(base):
        raise ValueError(
            f"Percorso plugin non consentito: '{base}' è fuori dall'area plugin "
            "runtime. I tool generati non possono mai scrivere nel codice sorgente "
            "dell'applicazione (solo gli sviluppatori possono promuovere un tool a "
            "nativo, manualmente, fuori da questo flusso)."
        )
    return base


def install_tool(
    name: str = "",
    doc: str = "",
    code: str = "",
    write: bool = False,
    ui: str = "",
    base_dir: Any = None,
    owner_user_id: Any = None,
    guardrails: Any = None,
) -> dict[str, Any]:
    """[WRITE] Scrive il plugin tra i sorgenti dopo validazione. Il catalogo tool
    viene ricaricato a caldo subito dopo: nessun riavvio del sistema è necessario.

    `base_dir` (opzionale) = area plugin del singolo utente (isolata). Se assente, usa
    la cartella runtime condivisa. Così i tool generati restano legati all'utente.
    `owner_user_id` (opzionale) = specchia il sorgente anche su Supabase (se
    configurata) legato a quell'utente, così sopravvive ai riavvii su Cloud Run
    (filesystem effimero). None = tool a scope globale.
    `guardrails` (opzionale) = oggetto `Guardrails` con policy su domini riservati
    e tool nativi. Se presente, impedisce:
      - shadowing di un tool nativo (stesso nome);
      - scrittura di un plugin in un dominio riservato (filesystem, shell, …).
    Se assente, i controlli di policy ricadono sul fallback hardcoded di `_validate_code`.

    Il risultato ritornato all'agente NON contiene il path assoluto del file:
    solo il nome del tool, lo stato, e un messaggio sintetico. Il path locale
    resta nei log del server.
    """
    name_s = _as_str(name)
    doc_s = _as_str(doc)
    code_s = _as_str(code)
    ui_s = _as_str(ui)
    try:
        write_b = bool(write)
    except Exception:  # noqa: BLE001
        write_b = False

    v = _validate_code(name_s, code_s, guardrails=guardrails)
    if v["errors"]:
        raise ValueError("Tool non installabile: " + "; ".join(v["errors"]))

    base = _resolve_base(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    stem = _module_stem(name_s)
    module_name = f"{stem}.py"
    path = base / module_name
    source = _plugin_source(name_s, doc_s, code_s, write_b)
    path.write_text(source, encoding="utf-8")

    # Verifica che il plugin appena scritto importi ed esponga il tool correttamente.
    loaded = {p.get("name"): p for p in load_plugins(base, pkg=f"hemmy._verify.{stem}")}
    entry = loaded.get(name_s)
    if entry is None or "error" in entry:
        err = (entry or {}).get("error", "non caricato")
        path.unlink(missing_ok=True)  # rollback: non lasciare un plugin rotto
        raise ValueError(f"Installazione annullata (rollback): il plugin non carica: {err}")

    db_warning = None
    try:
        save_plugin_to_db(
            module_name=module_name,
            source=source,
            manifest={"tools": [{"name": name_s, "doc": doc_s, "write": write_b, "entrypoint": "run"}]},
            owner_user_id=owner_user_id,
            created_by=owner_user_id,
        )
    except Exception as exc:  # noqa: BLE001 - non blocca l'installazione locale
        db_warning = f"persistenza su DB non riuscita ({exc}): il tool resta solo su questa istanza"

    result = {
        "installed": name_s,
        "write": write_b,
        "ui_change": ui_s or None,
        "reload_required": True,
        "message": (
            f"Tool '{name_s}' installato e attivo: il catalogo tool viene ricaricato "
            "a caldo automaticamente, subito dopo questo turno. Nessun riavvio del "
            "sistema è necessario."
        ),
    }
    if db_warning:
        result["db_warning"] = db_warning
    return result


def list_plugins(base_dir: Any = None) -> dict[str, Any]:
    """Elenca i plugin installati (tool generati a runtime) e il loro stato.

    NON ritorna il nome del modulo/file: solo il nome del tool (`<domain>.<action>`),
    il flag `write` e lo stato. Il path locale del file resta nei log del server.
    """
    base = _resolve_base(base_dir)
    specs = load_plugins(base, pkg=f"hemmy._list.{base.name}")
    ok = [{"name": p["name"], "write": p.get("write", False)} for p in specs if "error" not in p]
    broken = [{"name": p.get("name"), "error": p["error"]} for p in specs if "error" in p]
    return {"installed": ok, "broken": broken, "count": len(ok)}


def remove_plugin(
    name: str = "",
    base_dir: Any = None,
    owner_user_id: Any = None,
    guardrails: Any = None,
) -> dict[str, Any]:
    """[WRITE] Rimuove un plugin (per nome tool). Il catalogo tool viene
    ricaricato a caldo subito dopo: nessun riavvio del sistema è necessario.

    `guardrails` (opzionale) = oggetto `Guardrails`. Se presente, impedisce
    di cancellare un tool nativo: `guardrails.can_modify_tool(name)` deve
    ritornare True (i tool nativi hanno `immutable: true` in policy). Se
    assente, la funzione verifica che il file del plugin esista in `base_dir`
    — un tool nativo non ha file in `data/plugins/`, quindi la cancellazione
    fallisce naturalmente con FileNotFoundError.
    """
    name_s = _as_str(name)

    # Difesa esplicita: se `guardrails` è iniettato, rifiuta i tool nativi
    # PRIMA ancora di cercare il file (evita anche solo di toccare il filesystem).
    if guardrails is not None:
        try:
            if not guardrails.can_modify_tool(name_s):
                raise PermissionError(
                    f"Il tool '{name_s}' è nativo e non può essere rimosso da un "
                    "plugin utente. I tool nativi si possono usare e leggere, ma "
                    "non modificare né cancellare."
                )
        except PermissionError:
            raise
        except Exception:  # noqa: BLE001 - policy non disponibile: prosegui
            pass

    base = _resolve_base(base_dir)
    stem = _module_stem(name_s)
    path = base / f"{stem}.py"
    if not path.exists():
        # fallback: cerca per nome tool dentro i manifest caricati
        for p in load_plugins(base, pkg=f"hemmy._rm.{base.name}"):
            if p.get("name") == name_s and p.get("module"):
                path = base / p["module"]
                break
    if not path.exists():
        raise FileNotFoundError(f"Nessun plugin trovato per '{name_s}'.")
    module_name = path.name
    path.unlink()
    try:
        delete_plugin_from_db(module_name=module_name, owner_user_id=owner_user_id)
    except Exception:  # noqa: BLE001 - la rimozione locale resta valida comunque
        pass
    return {
        "removed": name_s,
        "reload_required": True,
        "message": (
            f"Plugin '{name_s}' rimosso e disattivato: il catalogo tool viene "
            "ricaricato a caldo automaticamente, nessun riavvio necessario."
        ),
    }