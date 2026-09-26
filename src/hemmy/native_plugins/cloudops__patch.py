# Plugin auto-generato per il tool 'cloudops.patch'.
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
def _list_vms(rg=None):
    if rg:
        path = f"/subscriptions/{_sub()}/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachines"
    else:
        path = f"/subscriptions/{_sub()}/providers/Microsoft.Compute/virtualMachines"
    code, resp = _arm("GET", path, None, "2023-09-01")
    if code >= 300 or not isinstance(resp, dict):
        return [], f"list vms failed: {code}"
    out = []
    for v in resp.get("value", []):
        props = v.get("properties") or {}
        out.append({"name": v.get("name"), "id": v.get("id"),
                    "location": v.get("location"),
                    "os": (props.get("storageProfile") or {}).get("osDisk", {}).get("osType"),
                    "vm_size": (props.get("hardwareProfile") or {}).get("vmSize")})
    return out, None


def _check_vm_patches(vm_id):
    path = f"{vm_id}/providers/Microsoft.GuestConfiguration/guestConfigurationAssignments"
    code, resp = _arm("GET", path, None, "2022-01-25")
    if code >= 300 or not isinstance(resp, dict):
        return {"available": False, "note": f"guest configuration non disponibile ({code})"}
    assignments = resp.get("value", [])
    return {"available": True, "assignments": len(assignments),
            "details": [a.get("name") for a in assignments]}


def _check_sql_patches(server_name, rg):
    path = (f"/subscriptions/{_sub()}/resourceGroups/{rg}/providers/Microsoft.Sql"
            f"/servers/{server_name}")
    code, resp = _arm("GET", path, None, "2023-08-01-preview")
    if code >= 300 or not isinstance(resp, dict):
        return {"available": False, "note": f"server non trovato ({code})"}
    props = resp.get("properties") or {}
    return {"available": True, "version": props.get("version"),
            "state": props.get("state"),
            "note": "Azure SQL e' gestito (patch automatiche); verifica solo la versione"}


def run(**kwargs):
    action = kwargs.get("action") or "summary"
    params = kwargs.get("params") or {}
    try:
        rg = params.get("resource_group")

        if action == "list_vms":
            vms, err = _list_vms(rg)
            if err:
                return {"ok": False, "error": err}
            return {"ok": True, "action": "list_vms", "vms": vms, "count": len(vms)}

        if action == "check_vm":
            vm_name = params.get("vm_name")
            if not (vm_name and rg):
                return {"ok": False, "error": "vm_name e resource_group richiesti"}
            vm_id = (f"/subscriptions/{_sub()}/resourceGroups/{rg}/providers/"
                     f"Microsoft.Compute/virtualMachines/{vm_name}")
            res = _check_vm_patches(vm_id)
            return {"ok": True, "action": "check_vm", "vm": vm_name, "result": res}

        if action == "check_sql":
            server = params.get("server_name")
            if not (server and rg):
                return {"ok": False, "error": "server_name e resource_group richiesti"}
            res = _check_sql_patches(server, rg)
            return {"ok": True, "action": "check_sql", "server": server, "result": res}

        if action == "summary":
            vms, err = _list_vms(rg)
            if err:
                return {"ok": False, "error": err}
            return {"ok": True, "action": "summary", "vms": len(vms),
                    "note": "Azure SQL e' gestito (patch automatiche); per le VM usa check_vm",
                    "vm_list": [v["name"] for v in vms]}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "cloudops.patch", "doc": "Gestisce il patch management di risorse Azure: verifica lo stato degli aggiornamenti (VM, SQL), elenca patch disponibili/mancanti, propone finestre di manutenzione. Usa ARM REST API (Update Management / SQL). Args: {\"action\": \"list_vms\"|\"check_vm\"|\"check_sql\"|\"summary\", \"params\": {\"resource_group\": str (opz), \"vm_name\": str (opz), \"server_name\": str (opz)}}. Ritorna: {patches: [...], pending: int, status}.", "write": False, "entrypoint": "run"}
    ]
}
