# Plugin auto-generato per il tool 'runbook.execute'.
# Installato via meta.install_tool con approvazione umana.

import os
import json


RUNBOOKS = {
    "db_blocked": {
        "description": "DB bloccato: rileva blocking, kill sessioni, notifica",
        "steps": [
            {"name": "detect_blocking", "tool": "sql.get_blocking", "params": {}},
            {"name": "notify", "tool": "notification.send",
             "params": {"message": "Blocking rilevato su SQL: intervento richiesto"}},
        ],
    },
    "cost_spike": {
        "description": "Picco di costo: rileva anomalia, identifica risorse, propone pause",
        "steps": [
            {"name": "detect_anomaly", "tool": "anomaly.detect", "params": {"action": "cost"}},
            {"name": "find_waste", "tool": "finops.waste", "params": {"action": "scan"}},
            {"name": "notify", "tool": "notification.send",
             "params": {"message": "Picco di costo rilevato: verifica risorse idle"}},
        ],
    },
    "storage_full": {
        "description": "Storage in esaurimento: verifica capacita, propone cleanup",
        "steps": [
            {"name": "check_capacity", "tool": "storage.get_capacity", "params": {}},
            {"name": "notify", "tool": "notification.send",
             "params": {"message": "Storage in esaurimento: valuta cleanup/lifecycle"}},
        ],
    },
    "pipeline_failed": {
        "description": "Pipeline ADF fallita: leggi run, attivita', proponi rerun",
        "steps": [
            {"name": "list_runs", "tool": "adf.get_pipeline_runs", "params": {}},
            {"name": "notify", "tool": "notification.send",
             "params": {"message": "Pipeline ADF fallita: verifica log"}},
        ],
    },
    "security_alert": {
        "description": "Alert di sicurezza: leggi alert Defender, valuta severita",
        "steps": [
            {"name": "list_alerts", "tool": "security.list_alerts", "params": {}},
            {"name": "notify", "tool": "notification.send",
             "params": {"message": "Alert di sicurezza attivi: verifica Defender"}},
        ],
    },
}


def run(**kwargs):
    action = kwargs.get("action") or "list"
    params = kwargs.get("params") or {}
    try:
        if action == "list":
            out = [{"name": k, "description": v["description"],
                    "steps": len(v["steps"])} for k, v in RUNBOOKS.items()]
            return {"ok": True, "action": "list", "runbooks": out, "count": len(out)}

        if action == "get":
            name = params.get("runbook")
            if name not in RUNBOOKS:
                return {"ok": False, "error": f"runbook non trovato: {name}"}
            return {"ok": True, "action": "get", "runbook": name, "definition": RUNBOOKS[name]}

        if action == "run":
            name = params.get("runbook")
            dry_run = params.get("dry_run", True)
            if name not in RUNBOOKS:
                return {"ok": False, "error": f"runbook non trovato: {name}"}
            rb = RUNBOOKS[name]
            steps = []
            for s in rb["steps"]:
                steps.append({"name": s["name"], "tool": s["tool"], "params": s["params"],
                              "status": "planned" if dry_run else "to_execute"})
            return {"ok": True, "action": "run", "runbook": name,
                    "description": rb["description"], "steps": steps,
                    "count": len(steps), "dry_run": dry_run,
                    "note": "gli step vanno eseguiti dai rispettivi tool (con approvazione)"}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "runbook.execute", "doc": "Esegue runbook/playbook codificati per incidenti comuni su Azure (es. 'DB bloccato -> kill sessioni -> notifica', 'storage pieno -> cleanup', 'costo anomalo -> pause risorse'). Ogni runbook e' una sequenza di step con guardrail. WRITE: ogni step passa dall'approvazione umana. Args: {\"action\": \"list\"|\"run\"|\"get\", \"params\": {\"runbook\": str, \"context\": {} (opz), \"dry_run\": bool (opz, default true)}}. Ritorna: {runbook, steps: [{name, tool, params, status}], status}.", "write": True, "entrypoint": "run"}
    ]
}
