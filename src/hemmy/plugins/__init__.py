"""Plugin di auto-estensione dell'agente.

Ogni file `*.py` in questa cartella (tranne questo) è un TOOL generato/approvato a
runtime tramite i tool `meta.*`. Un plugin è un modulo self-contained che espone un
`MANIFEST` con la dichiarazione COMPLETA del tool (nome, doc, se è scrittura, entrypoint):

    def run(**kwargs):
        ...
        return {...}   # risultato JSON-serializzabile

    MANIFEST = {
        "tools": [
            {"name": "domain.action", "doc": "...", "write": False, "entrypoint": "run"}
        ]
    }

Il loader (`load_plugins`) scopre i plugin all'avvio e li fonde in `tools`, `TOOL_DOCS`
e nella policy dei guardrail IN MEMORIA: così l'invariante tool↔doc↔policy resta sempre
soddisfatta e non serve modificare `cli.py`. Aggiungere/rimuovere un plugin NON richiede
un riavvio del processo: i tool `meta.*` segnalano `reload_required` e `web.py` ricarica
il catalogo a caldo, in-process, subito dopo il turno corrente.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
from pathlib import Path
from typing import Any

# Sicurezza: i tool generati a RUNTIME (dall'utente o dal meta-tooling) vivono
# FUORI dal codice sorgente dell'applicazione (`data/plugins/`, sotto la project
# root), mai dentro `src/hemmy/`. Solo gli sviluppatori possono promuovere un
# tool nella baseline nativa versionata (`native_plugins/`, dentro il sorgente):
# è un'azione manuale (commit + review), mai qualcosa che un utente o il modello
# possano fare a runtime tramite `meta.install_tool`. Questa separazione fisica è
# la seconda linea di difesa (la prima è che `base_dir`/`owner_user_id` non sono
# mai argomenti accettati dal modello — vedi `core/agent.py._RESERVED_ARG_NAMES`):
# anche in caso di un futuro bug di path handling, un tool generato non può
# fisicamente atterrare accanto al codice reale dell'agente.
_PACKAGE_DIR = Path(__file__).resolve().parent  # src/hemmy/plugins/ (sorgente)
_NATIVE_DIR = _PACKAGE_DIR.parent / "native_plugins"
_PROJECT_ROOT = _PACKAGE_DIR.resolve().parents[2]  # .../adf-agent (fuori da src/)
_RUNTIME_PLUGINS_DIR = _PROJECT_ROOT / "data" / "plugins"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


def plugins_dir() -> Path:
    """Cartella dei tool generati a RUNTIME (globali, non per-utente).

    Fuori dal codice sorgente (`data/plugins/`, sotto la project root) — MAI
    dentro `src/hemmy/`. Vedi il commento di modulo sul perché.
    """
    return _RUNTIME_PLUGINS_DIR


def native_plugins_dir() -> Path:
    """Cartella dei tool NATIVI (baseline condivisa, promossi e versionati).

    Questa SÌ è dentro il sorgente (`src/hemmy/native_plugins/`): è la
    baseline mantenuta dagli sviluppatori via commit/review, non scritta a
    runtime da nessun tool `meta.*`.
    """
    return _NATIVE_DIR


def user_plugins_dir(user_id: int | str) -> Path:
    """Cartella dei tool generati dal SINGOLO utente (isolati, non condivisi).

    Fuori dal codice sorgente, sotto `data/plugins/users/<id>/`.
    """
    return _RUNTIME_PLUGINS_DIR / "users" / str(user_id)


def is_inside_runtime_plugins_area(path: Path) -> bool:
    """True se `path` è contenuto in modo sicuro dentro l'area plugin RUNTIME
    (`data/plugins/`, mai dentro `src/hemmy/`). Usato come controllo di
    contenimento finale prima di scrivere/cancellare un file di plugin — difesa
    in profondità anche se un `base_dir` inatteso dovesse comunque arrivare."""
    try:
        resolved = path.resolve()
        runtime_root = _RUNTIME_PLUGINS_DIR.resolve()
    except OSError:
        return False
    if runtime_root not in resolved.parents and resolved != runtime_root:
        return False
    # Non deve mai risolvere dentro l'albero sorgente dell'applicazione.
    src_root = _PACKAGE_DIR.parent.parent  # .../src
    return src_root not in resolved.parents and resolved != src_root


def load_native_plugins() -> list[dict[str, Any]]:
    """Carica la baseline nativa (tool promossi, condivisi da tutti)."""
    return load_plugins(_NATIVE_DIR)


def load_user_plugins(user_id: int | str) -> list[dict[str, Any]]:
    """Carica i tool generati dall'utente indicato (solo la sua area isolata)."""
    return load_plugins(user_plugins_dir(user_id), pkg=f"hemmy._userplugins.u{user_id}")


def load_plugins(root: Path | None = None, pkg: str | None = None) -> list[dict[str, Any]]:
    """Scopre e carica i plugin da `root` (default: cartella runtime `plugins/`).

    `pkg` = prefisso del nome modulo (per evitare collisioni in sys.modules tra
    aree diverse, es. per-utente). Se omesso, deriva da `root.name`.

    Ritorna una lista di spec:

        {"name": str, "doc": str, "write": bool, "func": callable, "module": str}

    Un plugin malformato viene saltato con un warning nel campo `error` (non blocca
    l'avvio dell'app: un tool rotto non deve impedire l'uso di tutti gli altri).
    """
    base = root or _RUNTIME_PLUGINS_DIR
    if not base.is_dir():
        return []
    pkg = pkg or f"hemmy.{base.name}"
    specs: list[dict[str, Any]] = []
    for path in sorted(base.glob("*.py")):
        if path.name == "__init__.py":
            continue
        mod_name = f"{pkg}.{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            manifest = getattr(module, "MANIFEST", None)
            if not isinstance(manifest, dict):
                specs.append({"name": path.stem, "error": "MANIFEST mancante o non dict"})
                continue
            for t in manifest.get("tools", []):
                name = t.get("name", "")
                entry = t.get("entrypoint", "run")
                func = getattr(module, entry, None)
                if not _NAME_RE.match(name):
                    specs.append({"name": name or path.stem, "error": "nome tool non valido (atteso domain.action)"})
                    continue
                if not callable(func):
                    specs.append({"name": name, "error": f"entrypoint '{entry}' non trovato/non callable"})
                    continue
                specs.append({
                    "name": name,
                    "doc": str(t.get("doc", "")),
                    "write": bool(t.get("write", False)),
                    "func": func,
                    "module": path.name,
                })
        except Exception as exc:  # noqa: BLE001 - un plugin rotto non blocca l'avvio
            specs.append({"name": path.stem, "error": f"import fallito: {exc}"})
    return specs


def is_valid_tool_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))


# --------------------------------------------------------------- persistenza DB
# Il filesystem locale/`plugins/` è EFFIMERO su Cloud Run (si perde ad ogni
# restart/deploy, e non è condiviso tra istanze). Se Supabase è configurata, ogni
# scrittura di plugin viene specchiata anche a DB (sorgente + manifest); all'avvio
# li rimaterializziamo su disco PRIMA di importarli con `load_plugins`. Se Supabase
# non è configurata, queste funzioni sono no-op silenziosi: comportamento locale
# invariato (solo filesystem, come prima).


def save_plugin_to_db(
    *,
    module_name: str,
    source: str,
    manifest: dict[str, Any],
    owner_user_id: Any = None,
    created_by: Any = None,
) -> None:
    """Specchia un plugin appena scritto su disco anche su Supabase (best-effort)."""
    from hemmy.db.supabase_client import is_supabase_configured

    if not is_supabase_configured():
        return
    from hemmy.db.supabase_client import get_admin_client

    scope = "user" if owner_user_id is not None else "global"
    row: dict[str, Any] = {
        "scope": scope,
        "owner_user_id": owner_user_id,
        "module_name": module_name,
        "source_code": source,
        "manifest": manifest,
        "status": "active",
    }
    if created_by is not None:
        row["created_by"] = created_by

    # Niente ON CONFLICT/upsert: gli indici unique qui sono parziali (WHERE scope=...)
    # e PostgREST non può inferirli in automatico. Select-then-write esplicito.
    client = get_admin_client()
    q = client.table("plugins").select("id").eq("module_name", module_name).eq("scope", scope)
    q = q.eq("owner_user_id", owner_user_id) if owner_user_id is not None else q.is_("owner_user_id", "null")
    existing = q.execute().data or []
    if existing:
        client.table("plugins").update(row).eq("id", existing[0]["id"]).execute()
    else:
        client.table("plugins").insert(row).execute()


def delete_plugin_from_db(*, module_name: str, owner_user_id: Any = None) -> None:
    """Rimuove da Supabase il record del plugin cancellato da disco (best-effort)."""
    from hemmy.db.supabase_client import is_supabase_configured

    if not is_supabase_configured():
        return
    from hemmy.db.supabase_client import get_admin_client

    q = get_admin_client().table("plugins").delete().eq("module_name", module_name)
    if owner_user_id is not None:
        q = q.eq("owner_user_id", owner_user_id)
    else:
        q = q.eq("scope", "global")
    q.execute()


def _rematerialize(rows: list[dict[str, Any]], target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        name = row.get("module_name")
        source = row.get("source_code")
        if not name or not source:
            continue
        (target_dir / name).write_text(source, encoding="utf-8")


def sync_user_plugins_from_db(user_id: Any) -> None:
    """Riscrive su disco (area isolata dell'utente) i plugin salvati su Supabase.

    Va chiamata PRIMA di `load_user_plugins(user_id)`: su Cloud Run il disco è
    effimero, quindi ad ogni avvio i tool generati vanno recuperati dal DB.
    """
    from hemmy.db.supabase_client import is_supabase_configured

    if not is_supabase_configured():
        return
    from hemmy.db.supabase_client import get_admin_client

    resp = (
        get_admin_client()
        .table("plugins")
        .select("module_name,source_code")
        .eq("scope", "user")
        .eq("owner_user_id", user_id)
        .eq("status", "active")
        .execute()
    )
    _rematerialize(resp.data or [], user_plugins_dir(user_id))


def sync_global_plugins_from_db() -> None:
    """Come `sync_user_plugins_from_db`, ma per i tool a scope globale (condivisi)."""
    from hemmy.db.supabase_client import is_supabase_configured

    if not is_supabase_configured():
        return
    from hemmy.db.supabase_client import get_admin_client

    resp = (
        get_admin_client()
        .table("plugins")
        .select("module_name,source_code")
        .eq("scope", "global")
        .eq("status", "active")
        .execute()
    )
    _rematerialize(resp.data or [], _RUNTIME_PLUGINS_DIR)
