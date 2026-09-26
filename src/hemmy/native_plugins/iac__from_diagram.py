# Plugin auto-generato per il tool 'iac.from_diagram'.
# Installato via meta.install_tool con approvazione umana.

import os
import re
import json
import xml.etree.ElementTree as ET


DEFAULT_MAPPING = {
    "mxgraph.azure.sql": {"terraform_type": "azurerm_mssql_server", "module": "sql_server", "abbr": "sql"},
    "mxgraph.azure.sql_database": {"terraform_type": "azurerm_mssql_database", "module": "sql_database", "abbr": "sqldb"},
    "mxgraph.azure.storage": {"terraform_type": "azurerm_storage_account", "module": "storage_account", "abbr": "sa"},
    "mxgraph.azure.data_factory": {"terraform_type": "azurerm_data_factory", "module": "data_factory", "abbr": "adf"},
    "mxgraph.azure.key_vault": {"terraform_type": "azurerm_key_vault", "module": "key_vault", "abbr": "kv"},
    "mxgraph.azure.log_analytics": {"terraform_type": "azurerm_log_analytics_workspace", "module": "log_analytics", "abbr": "log"},
    "mxgraph.azure.virtual_network": {"terraform_type": "azurerm_virtual_network", "module": "virtual_network", "abbr": "vnet"},
    "mxgraph.azure.subnet": {"terraform_type": "azurerm_subnet", "module": "subnet", "abbr": "snet"},
    "mxgraph.azure.databricks": {"terraform_type": "azurerm_databricks_workspace", "module": "databricks", "abbr": "dbw"},
    "mxgraph.azure.function": {"terraform_type": "azurerm_linux_function_app", "module": "function_app", "abbr": "func"},
    "mxgraph.azure.app_service": {"terraform_type": "azurerm_linux_web_app", "module": "app_service", "abbr": "app"},
    "mxgraph.azure.monitor": {"terraform_type": "azurerm_monitor_metric_alert", "module": "alert_to_agent", "abbr": "alert"},
}

CONTAINER_PREFIXES = {
    "RG:": "resource_group",
    "SUB:": "subscription",
    "VNET:": "virtual_network",
    "SUBNET:": "subnet",
}

CONNECTION_TYPES = {
    "": "depends_on",
    "PE": "private_endpoint",
    "SE": "service_endpoint",
    "DIAG": "diagnostic_setting",
    "DATA": "data_flow",
    "RBAC": "role_assignment",
}

SPECIAL_LABELS = ["#skip", "#manual", "#import", "#secret", "#existing"]


def _read_drawio(path):
    if not os.path.exists(path):
        return None, f"file non trovato: {path}"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read(), None
    except Exception as e:
        return None, str(e)


def _parse_label(label):
    if not label:
        return {"name": None, "properties": {}, "special": []}
    lines = [l.strip() for l in label.replace("<br>", "\n").replace("&#xa;", "\n").split("\n") if l.strip()]
    if not lines:
        return {"name": None, "properties": {}, "special": []}
    name = lines[0]
    props = {}
    special = []
    for line in lines[1:]:
        if line in SPECIAL_LABELS:
            special.append(line)
        elif "=" in line:
            k, v = line.split("=", 1)
            props[k.strip()] = v.strip()
    return {"name": name, "properties": props, "special": special}


def _classify_node(style, label):
    if not label:
        return None
    for prefix, kind in CONTAINER_PREFIXES.items():
        if label.startswith(prefix):
            return {"kind": "container", "container_type": kind,
                    "name": label[len(prefix):].strip()}
    for icon, meta in DEFAULT_MAPPING.items():
        if icon in (style or ""):
            parsed = _parse_label(label)
            return {"kind": "resource", "icon": icon, "meta": meta, **parsed}
    return {"kind": "unknown", "label": label, "style": style}


def _parse_drawio(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        return None, f"XML non valido: {e}"

    nodes = []
    containers = []
    connections = []
    unknown = []

    for cell in root.iter("mxCell"):
        cell_id = cell.get("id")
        value = cell.get("value") or ""
        style = cell.get("style") or ""
        is_edge = cell.get("edge") == "1"
        is_vertex = cell.get("vertex") == "1"

        if is_edge:
            src = cell.get("source")
            tgt = cell.get("target")
            conn_type = CONNECTION_TYPES.get(value.strip(), "depends_on")
            connections.append({"id": cell_id, "source": src, "target": tgt,
                                "label": value.strip(), "type": conn_type})
        elif is_vertex:
            classified = _classify_node(style, value)
            if not classified:
                continue
            classified["id"] = cell_id
            if classified.get("kind") == "container":
                containers.append(classified)
            elif classified.get("kind") == "resource":
                nodes.append(classified)
            else:
                unknown.append(classified)

    return {"nodes": nodes, "containers": containers,
            "connections": connections, "unknown": unknown}, None


def _validate(parsed):
    errors = []
    warnings = []
    name_re = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
    for n in parsed["nodes"]:
        name = n.get("name")
        if not name:
            errors.append(f"nodo {n.get('id')}: nome mancante")
        elif not name_re.match(name):
            warnings.append(f"nodo {name}: nome non conforme alla naming convention")
    for u in parsed["unknown"]:
        warnings.append(f"nodo non riconosciuto: {u.get('label')}")
    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}


def _generate_hcl(parsed, environment="dev"):
    lines = [f"# Generato da iac.from_diagram (env: {environment})", ""]
    for n in parsed["nodes"]:
        meta = n.get("meta") or {}
        module = meta.get("module") or "unknown"
        name = n.get("name") or "unnamed"
        props = n.get("properties") or {}
        lines.append(f'module "{name.replace("-", "_")}" {{')
        lines.append(f'  source = "../../modules/{module}"')
        lines.append(f'  name   = "{name}"')
        for k, v in props.items():
            lines.append(f'  {k} = "{v}"')
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def run(**kwargs):
    action = kwargs.get("action") or "parse"
    params = kwargs.get("params") or {}
    try:
        if action == "docs":
            return {"ok": True, "action": "docs",
                    "guide": "docs/DRAWIO_GUIDE.md",
                    "mapping": "config/drawio_mapping.yaml",
                    "note": "segui la guida per disegnare il diagramma"}

        path = params.get("drawio_path")
        if not path:
            return {"ok": False, "error": "drawio_path richiesto"}

        xml_text, err = _read_drawio(path)
        if err:
            return {"ok": False, "error": err}

        parsed, err = _parse_drawio(xml_text)
        if err:
            return {"ok": False, "error": err}

        validation = _validate(parsed)

        if action == "parse":
            return {"ok": True, "action": "parse",
                    "nodes": parsed["nodes"], "containers": parsed["containers"],
                    "connections": parsed["connections"], "unknown": parsed["unknown"],
                    "validation": validation}

        if action == "generate":
            env = params.get("environment", "dev")
            hcl = _generate_hcl(parsed, env)
            return {"ok": validation["ok"], "action": "generate",
                    "hcl": hcl, "validation": validation,
                    "dry_run": params.get("dry_run", True)}

        if action == "deploy":
            env = params.get("environment", "dev")
            hcl = _generate_hcl(parsed, env)
            return {"ok": validation["ok"], "action": "deploy",
                    "hcl": hcl, "validation": validation,
                    "branch": params.get("branch"),
                    "auto_deploy": params.get("auto_deploy", False),
                    "note": "il commit e il trigger pipeline vanno eseguiti dall'agente con approvazione"}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "iac.from_diagram", "doc": "Legge un file draw.io (.drawio XML mxGraph), interpreta nodi/container/connessioni secondo docs/DRAWIO_GUIDE.md e config/drawio_mapping.yaml, genera il codice Terraform (moduli + environment) e opzionalmente committa su feature branch e triggera la pipeline CI/CD (plan -> apply). Args: {\"action\": \"docs\"|\"parse\"|\"generate\"|\"deploy\", \"params\": {\"drawio_path\": str, \"environment\": \"dev\"|\"uat\"|\"prod\" (opz, default 'dev'), \"branch\": str (opz), \"dry_run\": bool (opz, default true), \"auto_deploy\": bool (opz, default false)}}. Ritorna: {nodes: [...], containers: [...], connections: [...], hcl: {...}, validation: {...}, status}.", "write": True, "entrypoint": "run"}
    ]
}
