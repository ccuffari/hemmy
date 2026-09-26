# Plugin auto-generato per il tool 'resources.pause'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
import urllib.parse
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _arm_token():
    return get_arm_token()
def _arm(method, path, body=None, api_version=None):
    token = _arm_token()
    url = f"https://management.azure.com{path}"
    if api_version:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}api-version={api_version}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            detail = json.loads(raw)
        except Exception:
            detail = raw.decode(errors="replace")
        return e.code, detail


def _sub():
    return get_subscription_id()
def _pause_sql_database(rg, server, db, resume=False):
    path = (f"/subscriptions/{_sub()}/resourceGroups/{rg}/providers/Microsoft.Sql"
            f"/servers/{server}/databases/{db}")
    status = "Online" if resume else "Paused"
    code, resp = _arm("PATCH", path, {"properties": {"status": status}}, "2023-08-01-preview")
    return {"resource": f"sql/{server}/{db}", "action": "resume" if resume else "pause",
            "status_code": code, "response": resp}


def _pause_eventhubs_namespace(rg, namespace, resume=False):
    path = (f"/subscriptions/{_sub()}/resourceGroups/{rg}/providers/Microsoft.EventHub"
            f"/namespaces/{namespace}")
    body = {"properties": {"status": "Active" if resume else "Disabled"}}
    code, resp = _arm("PATCH", path, body, "2024-01-01")
    return {"resource": f"eventhubs/{namespace}", "action": "resume" if resume else "pause",
            "status_code": code, "response": resp}


def _adf_trigger(rg, factory, trigger, resume=False):
    action = "start" if resume else "stop"
    path = (f"/subscriptions/{_sub()}/resourceGroups/{rg}/providers/Microsoft.DataFactory"
            f"/factories/{factory}/triggers/{trigger}/{action}")
    code, resp = _arm("POST", path, {}, "2018-06-01")
    return {"resource": f"adf/{factory}/trigger/{trigger}", "action": action,
            "status_code": code, "response": resp}


def _dbx_token():
    # Segreto per-utente (Impostazioni), fallback a env var solo per CLI
    # locale senza portale (vedi auth.user_context).
    try:
        from hemmy.auth.user_context import get_current_user_secret_or_env

        return get_current_user_secret_or_env('databricks_token')
    except Exception:
        return os.environ.get('DATABRICKS_TOKEN')


def _databricks_cluster(workspace_url, cluster_id, resume=False):
    token = _dbx_token()
    if not token:
        raise RuntimeError("credenziale 'databricks_token' non configurata nelle Impostazioni")
    endpoint = "start" if resume else "delete"
    url = f"{workspace_url.rstrip('/')}/api/2.0/clusters/{endpoint}"
    body = {"cluster_id": cluster_id}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return {"resource": f"databricks/cluster/{cluster_id}",
                    "action": "start" if resume else "terminate",
                    "status_code": r.status, "response": json.loads(r.read() or b"{}")}
    except urllib.error.HTTPError as e:
        return {"resource": f"databricks/cluster/{cluster_id}",
                "action": "start" if resume else "terminate",
                "status_code": e.code, "response": e.read().decode(errors="replace")}


def run(**kwargs):
    action = kwargs.get("action")
    params = kwargs.get("params") or {}
    try:
        if action in ("pause", "resume"):
            resume = action == "resume"
            rtype = params.get("resource_type")
            if rtype == "sql_database":
                res = _pause_sql_database(params["resource_group"], params["server"], params["database"], resume)
            elif rtype == "eventhubs_namespace":
                res = _pause_eventhubs_namespace(params["resource_group"], params["namespace"], resume)
            elif rtype == "adf_trigger":
                res = _adf_trigger(params["resource_group"], params["factory"], params["trigger"], resume)
            elif rtype == "databricks_cluster":
                res = _databricks_cluster(params["workspace_url"], params["cluster_id"], resume)
            else:
                return {"ok": False, "error": f"resource_type non supportato: {rtype}"}
            return {"ok": res.get("status_code", 0) < 300, "action": action, "result": res}

        if action == "get_status":
            rtype = params.get("resource_type")
            if rtype == "sql_database":
                path = (f"/subscriptions/{_sub()}/resourceGroups/{params['resource_group']}/providers/Microsoft.Sql"
                        f"/servers/{params['server']}/databases/{params['database']}")
                code, resp = _arm("GET", path, None, "2023-08-01-preview")
                return {"ok": code < 300, "status_code": code,
                        "status": (resp.get("properties") or {}).get("status") if isinstance(resp, dict) else None,
                        "response": resp}
            return {"ok": False, "error": f"get_status non supportato per: {rtype}"}

        if action == "list_pausable":
            rg = params.get("resource_group")
            out = {"sql_databases": [], "eventhubs_namespaces": [], "adf_triggers": [], "databricks_workspaces": []}
            if rg:
                code, resp = _arm("GET", f"/subscriptions/{_sub()}/resourceGroups/{rg}/resources", None, "2021-04-01")
                if code < 300 and isinstance(resp, dict):
                    for it in resp.get("value", []):
                        t = (it.get("type") or "").lower()
                        if t == "microsoft.sql/servers/databases":
                            out["sql_databases"].append(it.get("name"))
                        elif t == "microsoft.eventhub/namespaces":
                            out["eventhubs_namespaces"].append(it.get("name"))
                        elif t == "microsoft.databricks/workspaces":
                            out["databricks_workspaces"].append(it.get("name"))
            return {"ok": True, "resource_group": rg, "pausable": out}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "resources.pause", "doc": "Mette in pausa (pause/suspend) o riprende (resume) risorse Azure per fermare i costi SENZA cancellarle. Usa ARM REST API (autenticazione ereditata dal processo). Azioni: pause, resume, get_status, list_pausable. Supporta: SQL Database (status=Paused/Online), Event Hubs namespace (disable/enable), ADF triggers (stop/start), Databricks cluster (terminate/start). Args: {\"action\": str, \"params\": {}}.", "write": True, "entrypoint": "run"}
    ]
}
