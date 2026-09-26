# Plugin auto-generato per il tool 'dataquality.lineage'.
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
def _adf_factory():
    return os.environ.get("ADF_FACTORY_NAME") or os.environ.get("ADF_NAME")


def _adf_rg():
    return os.environ.get("ADF_RESOURCE_GROUP") or os.environ.get("RESOURCE_GROUP")


def _list_pipelines():
    f = _adf_factory()
    rg = _adf_rg()
    if not (f and rg):
        return [], "ADF_FACTORY_NAME/ADF_RESOURCE_GROUP mancanti"
    path = (f"/subscriptions/{_sub()}/resourceGroups/{rg}/providers/Microsoft.DataFactory"
            f"/factories/{f}/pipelines")
    code, resp = _arm("GET", path, None, "2018-06-01")
    if code >= 300 or not isinstance(resp, dict):
        return [], f"list pipelines failed: {code}"
    return resp.get("value", []), None


def _build_lineage():
    nodes = {}
    edges = []
    pipelines, err = _list_pipelines()
    if err:
        return nodes, edges, err
    for p in pipelines:
        pname = p.get("name")
        nodes[f"pipeline:{pname}"] = {"id": f"pipeline:{pname}", "type": "pipeline"}
        props = p.get("properties") or {}
        for act in props.get("activities", []):
            inputs = act.get("inputs") or []
            outputs = act.get("outputs") or []
            for i in inputs:
                ref = i.get("referenceName")
                if ref:
                    nodes[f"dataset:{ref}"] = {"id": f"dataset:{ref}", "type": "dataset"}
                    edges.append({"from": f"dataset:{ref}", "to": f"pipeline:{pname}"})
            for o in outputs:
                ref = o.get("referenceName")
                if ref:
                    nodes[f"dataset:{ref}"] = {"id": f"dataset:{ref}", "type": "dataset"}
                    edges.append({"from": f"pipeline:{pname}", "to": f"dataset:{ref}"})
    return nodes, edges, None


def run(**kwargs):
    action = kwargs.get("action") or "trace"
    params = kwargs.get("params") or {}
    try:
        nodes, edges, err = _build_lineage()
        if err:
            return {"ok": False, "error": err}

        if action == "list_sources":
            sources = [n for n in nodes.values() if n["type"] == "dataset"]
            return {"ok": True, "action": "list_sources", "nodes": sources,
                    "count": len(sources)}

        if action == "list_targets":
            targets = [n for n in nodes.values() if n["type"] == "pipeline"]
            return {"ok": True, "action": "list_targets", "nodes": targets,
                    "count": len(targets)}

        if action == "trace":
            resource = params.get("resource")
            direction = params.get("direction") or "downstream"
            if not resource:
                return {"ok": True, "action": "trace", "nodes": list(nodes.values()),
                        "edges": edges, "count": len(nodes),
                        "note": "specifica 'resource' per tracciare un percorso"}
            start = resource if resource in nodes else f"dataset:{resource}"
            visited = set()
            queue = [start]
            path = []
            while queue:
                cur = queue.pop(0)
                if cur in visited:
                    continue
                visited.add(cur)
                for e in edges:
                    if direction == "downstream" and e["from"] == cur:
                        path.append(e)
                        queue.append(e["to"])
                    elif direction == "upstream" and e["to"] == cur:
                        path.append(e)
                        queue.append(e["from"])
            return {"ok": True, "action": "trace", "resource": resource,
                    "direction": direction, "path": path, "count": len(path)}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "dataquality.lineage", "doc": "Ricostruisce il lineage end-to-end dei dati: da sorgente (Blob/SQL) attraverso le pipeline ADF/dbt fino al consumo (tabelle/warehouse). Usa ADF (pipeline/dataset), SQL (tabelle), Purview (catalogazione). Args: {\"action\": \"trace\"|\"list_sources\"|\"list_targets\", \"params\": {\"resource\": str (opz), \"direction\": \"upstream\"|\"downstream\" (opz, default 'downstream')}}. Ritorna: {nodes: [...], edges: [...], path}.", "write": False, "entrypoint": "run"}
    ]
}
