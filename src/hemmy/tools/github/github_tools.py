"""Tool GitHub — scrittura diretta sul repository (senza working copy locale).

Committa uno o più file DIRETTAMENTE sul repo via Git Data API (un solo commit
atomico). Evita clone locale, git installato e problemi di credenziali; nel repo
finiscono solo i file scelti (niente vault).

Token e repo dal .env: GITHUB_TOKEN, GITHUB_REPO (owner/repo). Il token non è mai
passato al modello (redaction su stdout).

NB: per file dentro .github/workflows/ il token deve avere il permesso
'Workflows: Read and write' (fine-grained) o lo scope 'workflow' (classic).
"""

from __future__ import annotations

import base64
import fnmatch
import os
import re
from typing import Any

from hemmy.tools.cicd.cicd_tools import _http, resolve_github_token


def _settings() -> tuple[dict[str, str], str]:
    token = resolve_github_token()
    repo = os.environ.get("GITHUB_REPO", "")
    if not token or not repo:
        raise ValueError(
            "GitHub non autenticato: esegui 'gh auth login' e imposta GITHUB_REPO."
        )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "adf-agent",
        "Content-Type": "application/json",
    }
    return headers, repo


def _encrypt_secret(public_key_b64: str, value: str) -> str:
    """Cifra un valore con la public key del repo (libsodium sealed box)."""
    from nacl import encoding, public

    pk = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
    sealed = public.SealedBox(pk)
    encrypted = sealed.encrypt(value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("ascii")


def set_secret(secret_provider: Any, name: str) -> dict[str, Any]:
    """[WRITE] Crea/aggiorna un GitHub Actions secret nel repo.

    Il valore è fornito dall'operatore in modo sicuro (mai esposto al modello) e viene
    cifrato (sealed box) prima dell'invio. Utile per i secret richiesti dalla pipeline
    (es. ARM_* per Azure), definiti al momento della creazione.
    """
    headers, repo = _settings()
    _, key = _http(
        "GET", f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers
    )
    value = secret_provider(f"valore del GitHub Actions secret '{name}' (repo {repo})")
    if not value:
        raise ValueError("Nessun valore fornito dall'operatore: operazione annullata.")

    encrypted = _encrypt_secret(key["key"], value)
    status, _ = _http(
        "PUT",
        f"https://api.github.com/repos/{repo}/actions/secrets/{name}",
        {**headers, "Content-Type": "application/json"},
        {"encrypted_value": encrypted, "key_id": key["key_id"]},
    )
    return {"repo": repo, "secret": name, "http_status": status}


def get_default_branch() -> str:
    """Restituisce il branch di default del repo configurato."""
    headers, repo = _settings()
    _, data = _http("GET", f"https://api.github.com/repos/{repo}", headers)
    return data.get("default_branch", "main")


# ------------------------------------------------------------------ LETTURA repo
# Capacità di lettura del repo: senza queste GitHub sarebbe "write-only" e l'agente
# dovrebbe farsi incollare i file per correggerli (rischio di sovrascrittura cieca).


def get_file(path: str, ref: str | None = None) -> dict[str, Any]:
    """Legge un file dal repo e ne restituisce il contenuto testuale.

    path: percorso nel repo (es. 'modules/sql_server/main.tf').
    ref: branch/tag/sha opzionale (default: branch di default del repo).
    """
    headers, repo = _settings()
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    if ref:
        url += f"?ref={ref}"
    _, data = _http("GET", url, headers)
    if isinstance(data, list):
        raise ValueError(
            f"'{path}' è una directory, non un file. Usa github.list_directory."
        )
    if data.get("encoding") == "base64":
        text = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
    else:
        text = data.get("content", "")
    return {
        "repo": repo,
        "path": path,
        "sha": data.get("sha"),
        "size": data.get("size"),
        "content": text,
    }


def list_directory(path: str = "", ref: str | None = None) -> list[dict[str, Any]]:
    """Elenca il contenuto di una directory del repo (nome, tipo, dimensione).

    path vuoto = root del repo. Utile per capire cosa esiste prima di modificare.
    """
    headers, repo = _settings()
    url = f"https://api.github.com/repos/{repo}/contents/{path}".rstrip("/")
    if ref:
        url += f"?ref={ref}"
    _, data = _http("GET", url, headers)
    if not isinstance(data, list):  # è un file, non una directory
        return [
            {
                "name": data.get("name"),
                "path": data.get("path"),
                "type": data.get("type"),
                "size": data.get("size"),
            }
        ]
    return [
        {
            "name": e.get("name"),
            "path": e.get("path"),
            "type": e.get("type"),
            "size": e.get("size"),
        }
        for e in data
    ]


def get_tree(ref: str | None = None) -> dict[str, Any]:
    """Restituisce l'albero completo (ricorsivo) dei file del repo.

    ref: branch/tag/sha (default: branch di default). Vista d'insieme in un colpo.
    """
    headers, repo = _settings()
    tree_ref = ref or get_default_branch()
    _, data = _http(
        "GET",
        f"https://api.github.com/repos/{repo}/git/trees/{tree_ref}?recursive=1",
        headers,
    )
    entries = [
        {"path": e.get("path"), "type": e.get("type")} for e in data.get("tree", [])
    ]
    return {
        "repo": repo,
        "ref": tree_ref,
        "truncated": data.get("truncated", False),
        "tree": entries,
    }


# ---------------------------------------------------------- LETTURA BATCH / CARTELLE
# Evitano N chiamate a get_file quando serve ispezionare intere cartelle (moduli
# Terraform, pipeline CI/CD, ecc.). Applicano filtri e limiti anti-saturazione del
# contesto, così l'agente non riversa mai un intero repo nel prompt.


def _normalize_extensions(extensions: list[str] | None) -> list[str] | None:
    """Normalizza una lista di estensioni (aggiunge '.' se manca, minuscole)."""
    if not extensions:
        return None
    out: list[str] = []
    for ext in extensions:
        e = ext.strip().lower()
        if not e:
            continue
        if not e.startswith("."):
            e = "." + e
        out.append(e)
    return out or None


def _match_extensions(path: str, extensions: list[str] | None) -> bool:
    """True se path ha una delle estensioni richieste (o nessun filtro)."""
    if not extensions:
        return True
    lower = path.lower()
    return any(lower.endswith(ext) for ext in extensions)


def _is_binary_path(path: str) -> bool:
    """Euristica: esclude binari comuni dalla lettura batch (evita contenuti illeggibili)."""
    binary_exts = (
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".tar", ".gz",
        ".7z", ".rar", ".exe", ".dll", ".so", ".dylib", ".pyc", ".class", ".jar",
        ".woff", ".woff2", ".ttf", ".eot", ".mp3", ".mp4", ".mov", ".avi", ".bin",
    )
    return path.lower().endswith(binary_exts)


def _fetch_file_text(
    repo: str, headers: dict[str, str], path: str, ref: str | None = None
) -> str:
    """Scarica il contenuto testuale di un file dal repo (blob API o contents API)."""
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    if ref:
        url += f"?ref={ref}"
    _, data = _http("GET", url, headers)
    if isinstance(data, list):
        raise ValueError(f"'{path}' è una directory, non un file.")
    if data.get("encoding") == "base64":
        return base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
    return data.get("content", "")


def read_folder(
    path: str,
    recursive: bool = True,
    extensions: list[str] | None = None,
    max_files: int = 50,
    max_bytes_per_file: int = 200_000,
    ref: str | None = None,
) -> dict[str, Any]:
    """Legge una CARTELLA del repo e restituisce TUTTI i file con il contenuto.

    Un'unica chiamata (evita N chiamate a github.get_file) pensata per ispezionare
    moduli Terraform, pipeline CI/CD, cartelle di config, ecc. senza clonare il repo.

    path: cartella di partenza (es. 'infra/modules/sql_server'); '' = root.
    recursive: se True scende nelle sottocartelle (default True).
    extensions: filtro opzionale sulle estensioni, es. ['.tf', '.yaml'] o ['tf'].
    max_files: numero massimo di file restituiti (default 50, anti-saturazione).
    max_bytes_per_file: troncamento per file (default 200_000 byte ≈ 200 KB).
    ref: branch/tag/sha (default: branch di default del repo).

    Restituisce: {repo, ref, path, count, truncated, files: [{path, size, content, truncated}]}.
    """
    headers, repo = _settings()
    base_ref = ref or get_default_branch()
    exts = _normalize_extensions(extensions)

    # 1. Ottieni l'albero ricorsivo del repo (una sola chiamata).
    _, tree_data = _http(
        "GET",
        f"https://api.github.com/repos/{repo}/git/trees/{base_ref}?recursive=1",
        headers,
    )
    tree = tree_data.get("tree", [])
    tree_truncated = bool(tree_data.get("truncated", False))

    # 2. Filtra per prefisso + tipo blob + estensione + esclusione binari.
    prefix = path.strip("/")
    prefix_slash = (prefix + "/") if prefix else ""

    candidates: list[dict[str, Any]] = []
    for entry in tree:
        if entry.get("type") != "blob":
            continue
        epath: str = entry.get("path") or ""
        if prefix:
            if recursive:
                if not epath.startswith(prefix_slash):
                    continue
            else:
                # Solo file diretti nella cartella (nessuna '/' dopo il prefisso).
                if not epath.startswith(prefix_slash):
                    continue
                remainder = epath[len(prefix_slash):]
                if "/" in remainder:
                    continue
        if _is_binary_path(epath):
            continue
        if not _match_extensions(epath, exts):
            continue
        candidates.append({"path": epath, "size": entry.get("size")})

    total_matched = len(candidates)
    truncated_by_count = total_matched > max_files
    candidates = candidates[:max_files]

    # 3. Scarica il contenuto di ogni file selezionato (con troncamento per file).
    files: list[dict[str, Any]] = []
    total_bytes = 0
    errors: list[dict[str, str]] = []
    for c in candidates:
        p = c["path"]
        try:
            text = _fetch_file_text(repo, headers, p, base_ref)
        except Exception as exc:  # noqa: BLE001 - un file non deve rompere la cartella
            errors.append({"path": p, "error": f"{type(exc).__name__}: {exc}"})
            continue
        was_truncated = len(text.encode("utf-8")) > max_bytes_per_file
        if was_truncated:
            # Tronca su confine di byte sicuro per UTF-8.
            text = text.encode("utf-8")[:max_bytes_per_file].decode("utf-8", errors="ignore")
            text += f"\n\n[... TRONCATO a {max_bytes_per_file} byte ...]"
        total_bytes += len(text.encode("utf-8"))
        files.append(
            {
                "path": p,
                "size": c.get("size"),
                "truncated": was_truncated,
                "content": text,
            }
        )

    return {
        "repo": repo,
        "ref": base_ref,
        "path": prefix or "/",
        "recursive": recursive,
        "extensions": exts,
        "count": len(files),
        "total_matched": total_matched,
        "total_bytes": total_bytes,
        "truncated": truncated_by_count or tree_truncated,
        "tree_truncated": tree_truncated,
        "errors": errors,
        "files": files,
    }


def get_files(
    paths: list[str],
    ref: str | None = None,
    max_bytes_per_file: int = 200_000,
) -> dict[str, Any]:
    """Legge PIÙ file del repo in un'unica chiamata batch (paths espliciti).

    paths: lista di percorsi nel repo.
    ref: branch/tag/sha (default: branch di default).
    max_bytes_per_file: troncamento per file.

    Restituisce: {repo, ref, count, files: [{path, size, content, truncated}], errors}.
    """
    if not paths:
        raise ValueError("Nessun path fornito.")
    headers, repo = _settings()
    base_ref = ref or get_default_branch()

    files: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    total_bytes = 0
    for p in paths:
        try:
            text = _fetch_file_text(repo, headers, p, base_ref)
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": p, "error": f"{type(exc).__name__}: {exc}"})
            continue
        was_truncated = len(text.encode("utf-8")) > max_bytes_per_file
        if was_truncated:
            text = text.encode("utf-8")[:max_bytes_per_file].decode("utf-8", errors="ignore")
            text += f"\n\n[... TRONCATO a {max_bytes_per_file} byte ...]"
        total_bytes += len(text.encode("utf-8"))
        files.append(
            {
                "path": p,
                "truncated": was_truncated,
                "content": text,
            }
        )

    return {
        "repo": repo,
        "ref": base_ref,
        "count": len(files),
        "total_bytes": total_bytes,
        "files": files,
        "errors": errors,
    }


def search_code(
    query: str,
    path: str | None = None,
    extensions: list[str] | None = None,
    max_results: int = 20,
    ref: str | None = None,
    case_sensitive: bool = False,
    use_regex: bool = False,
) -> dict[str, Any]:
    """Cerca codice nel repo (query testuale o regex) e restituisce path + righe match.

    Implementazione lato client: scorre l'albero del repo, filtra per prefisso ed
    estensione, scarica i file e cerca la query. Adatto a repo di piccole/medie
    dimensioni; per repo molto grandi preferisci github.read_folder mirato.

    query: testo o pattern regex da cercare.
    path: sottocartella di partenza (es. 'infra/modules'); None = root.
    extensions: filtro estensioni (es. ['.tf', '.yml']).
    max_results: numero massimo di match restituiti (default 20).
    ref: branch/tag/sha (default: branch di default).
    case_sensitive: se True, distingue maiuscole/minuscole.
    use_regex: se True interpreta 'query' come regex Python.

    Restituisce: {repo, ref, query, matches: [{path, line, text}], total_matches, truncated}.
    """
    if not query:
        raise ValueError("Query vuota.")
    headers, repo = _settings()
    base_ref = ref or get_default_branch()
    exts = _normalize_extensions(extensions)

    try:
        pattern = re.compile(query, 0 if case_sensitive else re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"Regex non valida: {exc}") from exc

    # 1. Albero ricorsivo.
    _, tree_data = _http(
        "GET",
        f"https://api.github.com/repos/{repo}/git/trees/{base_ref}?recursive=1",
        headers,
    )
    tree = tree_data.get("tree", [])

    prefix = (path or "").strip("/")
    prefix_slash = (prefix + "/") if prefix else ""

    matches: list[dict[str, Any]] = []
    total_matches = 0
    files_scanned = 0
    truncated = False

    for entry in tree:
        if entry.get("type") != "blob":
            continue
        epath: str = entry.get("path") or ""
        if prefix and not epath.startswith(prefix_slash):
            continue
        if _is_binary_path(epath):
            continue
        if not _match_extensions(epath, exts):
            continue

        try:
            text = _fetch_file_text(repo, headers, epath, base_ref)
        except Exception:  # noqa: BLE001 - salta file non leggibili
            continue
        files_scanned += 1
        for i, line in enumerate(text.splitlines(), start=1):
            hay = line if case_sensitive else line
            if use_regex:
                found = bool(pattern.search(hay))
            else:
                needle = query if case_sensitive else query.lower()
                found = needle in (hay if case_sensitive else hay.lower())
            if found:
                total_matches += 1
                if len(matches) < max_results:
                    matches.append(
                        {"path": epath, "line": i, "text": line.strip()[:300]}
                    )
                else:
                    truncated = True
        if len(matches) >= max_results and not use_regex:
            # Non interrompere subito: continuiamo a contare i match senza accumulare.
            pass

    return {
        "repo": repo,
        "ref": base_ref,
        "query": query,
        "use_regex": use_regex,
        "case_sensitive": case_sensitive,
        "files_scanned": files_scanned,
        "total_matches": total_matches,
        "returned": len(matches),
        "truncated": truncated,
        "matches": matches,
    }


def get_latest_run(workflow: str | None = None) -> dict[str, Any]:
    """Ultima esecuzione della CI (opz. filtrata per file workflow, es. 'terraform-dev.yml').

    Comodo per collegare velocemente diagnosi → get_run_logs senza cercare il run id.
    """
    headers, repo = _settings()
    if workflow:
        url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs?per_page=1"
    else:
        url = f"https://api.github.com/repos/{repo}/actions/runs?per_page=1"
    _, data = _http("GET", url, headers)
    runs = data.get("workflow_runs", [])
    if not runs:
        return {"repo": repo, "run": None}
    r = runs[0]
    return {
        "repo": repo,
        "run": {
            "id": r.get("id"),
            "name": r.get("name"),
            "status": r.get("status"),
            "conclusion": r.get("conclusion"),
            "created_at": r.get("created_at"),
            "head_branch": r.get("head_branch"),
        },
    }


def get_commit(sha: str = "HEAD") -> dict[str, Any]:
    """Dettaglio di un commit: messaggio, autore, data e file toccati (con stato).

    sha: SHA completo/abbreviato o ref (default 'HEAD'). Utile per capire cosa è
    cambiato in un commit prima di intervenire.
    """
    headers, repo = _settings()
    _, data = _http("GET", f"https://api.github.com/repos/{repo}/commits/{sha}", headers)
    commit = data.get("commit", {})
    files = [
        {
            "filename": f.get("filename"),
            "status": f.get("status"),
            "additions": f.get("additions"),
            "deletions": f.get("deletions"),
        }
        for f in (data.get("files") or [])
    ]
    return {
        "repo": repo,
        "sha": data.get("sha"),
        "message": commit.get("message"),
        "author": (commit.get("author") or {}).get("name"),
        "date": (commit.get("author") or {}).get("date"),
        "files": files,
    }


def get_diff(base: str, head: str) -> dict[str, Any]:
    """Confronto tra due ref (base...head): file modificati con patch unificata.

    base/head: branch, tag o SHA. Comodo per rivedere cosa cambia prima di un apply,
    o per ispezionare la differenza tra due versioni del codice IaC.
    """
    headers, repo = _settings()
    _, data = _http(
        "GET", f"https://api.github.com/repos/{repo}/compare/{base}...{head}", headers
    )
    files = [
        {
            "filename": f.get("filename"),
            "status": f.get("status"),
            "additions": f.get("additions"),
            "deletions": f.get("deletions"),
            "patch": f.get("patch"),
        }
        for f in (data.get("files") or [])
    ]
    return {
        "repo": repo,
        "base": base,
        "head": head,
        "status": data.get("status"),
        "ahead_by": data.get("ahead_by"),
        "behind_by": data.get("behind_by"),
        "total_commits": data.get("total_commits"),
        "files": files,
    }


def get_workflow(workflow: str | None = None) -> dict[str, Any]:
    """Metadati dei workflow GitHub Actions del repo (nome, path, stato).

    workflow: file o id di un workflow specifico (es. 'terraform-dev.yml'); se omesso,
    elenca tutti i workflow del repo. Per il CONTENUTO YAML usa github.get_file.
    """
    headers, repo = _settings()
    if workflow:
        _, w = _http(
            "GET",
            f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}",
            headers,
        )
        return {
            "repo": repo,
            "workflow": {
                "id": w.get("id"),
                "name": w.get("name"),
                "path": w.get("path"),
                "state": w.get("state"),
            },
        }
    _, data = _http("GET", f"https://api.github.com/repos/{repo}/actions/workflows", headers)
    return {
        "repo": repo,
        "workflows": [
            {"id": w.get("id"), "name": w.get("name"), "path": w.get("path"), "state": w.get("state")}
            for w in (data.get("workflows") or [])
        ],
    }


def _put_content(
    repo: str,
    headers: dict[str, str],
    path: str,
    content: str,
    message: str,
    branch: str | None = None,
) -> Any:
    body: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    }
    if branch:
        body["branch"] = branch
    _, data = _http(
        "PUT", f"https://api.github.com/repos/{repo}/contents/{path}", headers, body
    )
    return data


def _default_head_sha(repo: str, headers: dict[str, str]) -> str | None:
    """SHA dell'HEAD del branch di default; None se il repo è VUOTO (nessun commit).

    Serve a distinguere "repo vuoto" (→ bootstrap) da "branch mancante" (→ crea branch).
    """
    try:
        default = get_default_branch()
    except RuntimeError:
        return None
    try:
        _, ref = _http(
            "GET", f"https://api.github.com/repos/{repo}/git/ref/heads/{default}", headers
        )
        return ref["object"]["sha"]
    except RuntimeError:
        return None


def list_branches() -> dict[str, Any]:
    """Elenca i branch del repo (nome, protetto, sha) e indica il branch di default."""
    headers, repo = _settings()
    _, data = _http(
        "GET", f"https://api.github.com/repos/{repo}/branches?per_page=100", headers
    )
    branches = [
        {
            "name": b.get("name"),
            "protected": b.get("protected"),
            "sha": (b.get("commit") or {}).get("sha"),
        }
        for b in (data if isinstance(data, list) else [])
    ]
    default = None
    try:
        default = get_default_branch()
    except RuntimeError:
        pass
    return {
        "repo": repo,
        "default_branch": default,
        "count": len(branches),
        "branches": branches,
    }


def create_branch(branch: str, from_ref: str | None = None) -> dict[str, Any]:
    """[WRITE] Crea un nuovo branch dal branch di default (o da from_ref).

    branch: nome del nuovo branch (es. 'feature/rg-demo').
    from_ref: sorgente opzionale — nome branch, tag o SHA. Default: branch di default.
    Idempotente: se il branch esiste già lo segnala senza errore.
    """
    if not branch:
        raise ValueError("Nome del branch mancante.")
    headers, repo = _settings()
    base = f"https://api.github.com/repos/{repo}/git"
    src = from_ref or get_default_branch()

    # Risolvi lo SHA sorgente: prima come branch, poi come commit-ish (sha/tag).
    try:
        _, ref = _http("GET", f"{base}/ref/heads/{src}", headers)
        sha = ref["object"]["sha"]
    except RuntimeError:
        _, commit = _http(
            "GET", f"https://api.github.com/repos/{repo}/commits/{src}", headers
        )
        sha = commit.get("sha")
    if not sha:
        raise RuntimeError(f"Impossibile risolvere il ref sorgente '{src}'.")

    try:
        _http("POST", f"{base}/refs", headers, {"ref": f"refs/heads/{branch}", "sha": sha})
    except RuntimeError as exc:
        if "422" in str(exc):  # 'Reference already exists'
            return {"repo": repo, "branch": branch, "from": src, "sha": sha, "already_exists": True}
        raise
    return {"repo": repo, "branch": branch, "from": src, "sha": sha, "created": True}


def delete_branch(branch: str) -> dict[str, Any]:
    """[WRITE] Elimina un branch dal repo (es. cleanup dopo il merge di una PR).

    branch: nome del branch da eliminare (mai il branch di default). Idempotente:
    se il branch non esiste lo segnala senza errore.
    """
    if not branch:
        raise ValueError("Nome del branch mancante.")
    headers, repo = _settings()
    default = get_default_branch()
    if branch == default:
        raise ValueError(f"Rifiuto di eliminare il branch di default '{default}'.")
    try:
        _http(
            "DELETE",
            f"https://api.github.com/repos/{repo}/git/refs/heads/{branch}",
            headers,
        )
    except RuntimeError as exc:
        if "404" in str(exc) or "422" in str(exc):
            return {"repo": repo, "branch": branch, "deleted": False, "not_found": True}
        raise
    return {"repo": repo, "branch": branch, "deleted": True}


# ---------------------------------------------------------------- Pull Request
# Flusso GitFlow: feature branch -> PR verso dev -> PR dev->uat -> PR uat->prod.
# Ogni PROMOZIONE è una PR che l'operatore approva/mergia (mai automatica).


def create_pull_request(
    head: str,
    base: str,
    title: str,
    body: str = "",
    draft: bool = False,
) -> dict[str, Any]:
    """[WRITE] Apre una Pull Request da 'head' verso 'base'.

    head: branch sorgente (es. 'feature/x' o 'dev' per la promozione dev->uat).
    base: branch di destinazione (es. 'dev', 'uat', 'prod').
    title/body: titolo e descrizione. draft=True per aprirla come bozza.
    Se una PR aperta head->base esiste già, la restituisce invece di duplicarla.
    """
    if not head or not base:
        raise ValueError("Servono sia 'head' sia 'base'.")
    if head == base:
        raise ValueError("'head' e 'base' non possono coincidere.")
    headers, repo = _settings()
    try:
        _, data = _http(
            "POST",
            f"https://api.github.com/repos/{repo}/pulls",
            headers,
            {"title": title, "head": head, "base": base, "body": body, "draft": draft},
        )
    except RuntimeError as exc:
        # 422 = PR già esistente per questa coppia head->base: la ritrovo.
        if "422" in str(exc):
            owner = repo.split("/", 1)[0]
            _, existing = _http(
                "GET",
                f"https://api.github.com/repos/{repo}/pulls?state=open&head={owner}:{head}&base={base}",
                headers,
            )
            if isinstance(existing, list) and existing:
                pr = existing[0]
                return {
                    "repo": repo,
                    "number": pr.get("number"),
                    "url": pr.get("html_url"),
                    "state": pr.get("state"),
                    "head": head,
                    "base": base,
                    "already_exists": True,
                }
        raise
    return {
        "repo": repo,
        "number": data.get("number"),
        "url": data.get("html_url"),
        "state": data.get("state"),
        "head": head,
        "base": base,
        "created": True,
    }


def merge_pull_request(number: int, method: str = "merge") -> dict[str, Any]:
    """[WRITE] Mergia una Pull Request (promozione approvata dall'operatore).

    number: numero della PR. method: 'merge' | 'squash' | 'rebase'.
    Fallisce con un messaggio chiaro se la PR non è mergiabile (conflitti, check
    non superati, review mancante): la promozione va risolta prima.
    """
    if method not in {"merge", "squash", "rebase"}:
        raise ValueError("method deve essere 'merge', 'squash' o 'rebase'.")
    headers, repo = _settings()
    _, pr = _http("GET", f"https://api.github.com/repos/{repo}/pulls/{number}", headers)
    if pr.get("merged"):
        return {"repo": repo, "number": number, "merged": True, "already_merged": True}
    mergeable = pr.get("mergeable")
    if mergeable is False:
        raise RuntimeError(
            f"PR #{number} non mergiabile (conflitti o check non superati). "
            "Risolvi i conflitti/allinea il branch prima di promuovere."
        )
    _, data = _http(
        "PUT",
        f"https://api.github.com/repos/{repo}/pulls/{number}/merge",
        headers,
        {"merge_method": method},
    )
    return {
        "repo": repo,
        "number": number,
        "merged": bool(data.get("merged")),
        "sha": data.get("sha"),
        "message": data.get("message"),
    }


def list_pull_requests(state: str = "open", base: str | None = None) -> dict[str, Any]:
    """Elenca le Pull Request del repo (stato: open|closed|all, filtro base opz.)."""
    headers, repo = _settings()
    url = f"https://api.github.com/repos/{repo}/pulls?state={state}&per_page=50"
    if base:
        url += f"&base={base}"
    _, data = _http("GET", url, headers)
    prs = [
        {
            "number": p.get("number"),
            "title": p.get("title"),
            "state": p.get("state"),
            "head": (p.get("head") or {}).get("ref"),
            "base": (p.get("base") or {}).get("ref"),
            "draft": p.get("draft"),
            "url": p.get("html_url"),
        }
        for p in (data if isinstance(data, list) else [])
    ]
    return {"repo": repo, "state": state, "count": len(prs), "pull_requests": prs}


def get_pull_request(number: int) -> dict[str, Any]:
    """Dettaglio di una Pull Request: stato, mergiabilità, branch, conteggi file."""
    headers, repo = _settings()
    _, p = _http("GET", f"https://api.github.com/repos/{repo}/pulls/{number}", headers)
    return {
        "repo": repo,
        "number": p.get("number"),
        "title": p.get("title"),
        "state": p.get("state"),
        "merged": p.get("merged"),
        "mergeable": p.get("mergeable"),
        "mergeable_state": p.get("mergeable_state"),
        "head": (p.get("head") or {}).get("ref"),
        "base": (p.get("base") or {}).get("ref"),
        "commits": p.get("commits"),
        "changed_files": p.get("changed_files"),
        "additions": p.get("additions"),
        "deletions": p.get("deletions"),
        "url": p.get("html_url"),
    }


def _bootstrap_commit(
    repo: str, headers: dict[str, str], files: dict[str, str], message: str, branch: str
) -> dict[str, Any]:
    """Repo vuoto: crea i file via Contents API (inizializza il branch)."""
    committed = []
    use_branch: str | None = None  # primo file: crea il branch di default
    for path, content in files.items():
        _put_content(repo, headers, path, content, message, use_branch)
        committed.append(path)
        use_branch = branch  # i successivi puntano al branch ormai esistente
    return {"repo": repo, "branch": branch, "committed": committed, "bootstrap": True}


def commit_files(
    files: dict[str, str],
    message: str,
    branch: str = "main",
    create_branch_if_missing: bool = True,
) -> dict[str, Any]:
    """[WRITE] Committa più file direttamente sul repo GitHub.

    files: mappa {path_nel_repo: contenuto}. Su repo NON vuoto crea un unico commit
    atomico (Git Data API); su repo VUOTO fa il bootstrap via Contents API.

    Gestione del branch:
    - branch esistente -> commit su quel branch;
    - branch MANCANTE su repo popolato -> lo crea dal branch di default e committa lì
      (se create_branch_if_missing=True, default); altrimenti solleva un errore chiaro;
    - repo VUOTO -> bootstrap che inizializza il branch richiesto.
    """
    if not files:
        raise ValueError("Nessun file da committare.")

    headers, repo = _settings()
    base = f"https://api.github.com/repos/{repo}/git"

    # 1. ref del branch -> commit di base -> tree di base.
    try:
        _, ref = _http("GET", f"{base}/ref/heads/{branch}", headers)
        base_sha = ref["object"]["sha"]
    except RuntimeError:
        # Il branch richiesto non esiste: distinguo "repo vuoto" da "branch mancante".
        default_sha = _default_head_sha(repo, headers)
        if default_sha is None:
            # Repo genuinamente vuoto (nessun commit): bootstrap via Contents API.
            return _bootstrap_commit(repo, headers, files, message, branch)
        # Repo popolato ma branch inesistente: crealo dal default, poi committa lì.
        if not create_branch_if_missing:
            raise RuntimeError(
                f"Branch '{branch}' inesistente nel repo {repo}. Crealo con "
                "github.create_branch, oppure passa create_branch_if_missing=true."
            )
        _http(
            "POST",
            f"{base}/refs",
            headers,
            {"ref": f"refs/heads/{branch}", "sha": default_sha},
        )
        base_sha = default_sha
    _, base_commit = _http("GET", f"{base}/commits/{base_sha}", headers)
    base_tree = base_commit["tree"]["sha"]

    # 2. nuovo tree con i file (contenuto inline)
    tree = [
        {"path": path, "mode": "100644", "type": "blob", "content": content}
        for path, content in files.items()
    ]
    _, new_tree = _http("POST", f"{base}/trees", headers, {"base_tree": base_tree, "tree": tree})

    # 3. commit + spostamento del ref
    _, new_commit = _http(
        "POST",
        f"{base}/commits",
        headers,
        {"message": message, "tree": new_tree["sha"], "parents": [base_sha]},
    )
    _http("PATCH", f"{base}/refs/heads/{branch}", headers, {"sha": new_commit["sha"]})

    return {
        "repo": repo,
        "branch": branch,
        "committed": list(files),
        "commit_sha": new_commit["sha"],
    }