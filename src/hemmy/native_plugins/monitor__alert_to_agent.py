# Plugin auto-generato per il tool 'monitor.alert_to_agent'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
import urllib.parse


DIAGNOSIS_MAPPINGS = {
    "dtu_consumption_percent": ["sql.get_dtu_usage", "sql.get_query_stats", "sql.get_blocking"],
    "cpu_percent": ["sql.get_wait_stats", "sql.get_query_stats"],
    "used_capacity": ["storage.get_capacity", "storage.get_lifecycle_policy"],
    "PipelineFailedRuns": ["adf.get_pipeline_runs", "adf.get_activity_runs"],
    "ActualCost": ["anomaly.detect", "finops.waste"],
    "AppExceptions": ["monitor.anomaly_logs"],
    "security": ["security.list_alerts"],
}


def _extract_context(payload):
    data = payload.get("data") or {}
    essentials = data.get("essentials") or {}
    alert_context = data.get("alertContext") or {}
    condition = alert_context.get("condition") or {}
    return {
        "alert_name": essentials.get("alertRule"),
        "severity": essentials.get("severity"),
        "monitor_condition": essentials.get("monitorCondition"),
        "fired_time": essentials.get("firedDateTime"),
        "resource_ids": essentials.get("configurationItems") or [],
        "metric_name": condition.get("metricName"),
        "threshold": condition.get("threshold"),
        "operator": condition.get("operator"),
        "alert_context": alert_context,
    }


def _select_diagnosis_tools(context):
    metric = context.get("metric_name") or ""
    alert_name = (context.get("alert_name") or "").lower()
    tools = []
    for key, mapped in DIAGNOSIS_MAPPINGS.items():
        if key.lower() in metric.lower() or key.lower() in alert_name:
            tools.extend(mapped)
    if not tools:
        tools = ["monitor.get_metrics"]
    seen = set()
    out = []
    for t in tools:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _build_remediation_plan(context, diagnosis_tools):
    metric = context.get("metric_name") or "unknown"
    plan = [
        {"step": 1, "action": "diagnose",
         "detail": f"esegui i tool di diagnosi: {', '.join(diagnosis_tools)}"},
        {"step": 2, "action": "analyze",
         "detail": f"analizza i risultati per la metrica '{metric}'"},
        {"step": 3, "action": "propose_fix",
         "detail": "genera la proposta di remediation (remediation.run plan)"},
        {"step": 4, "action": "notify",
         "detail": "invia la proposta via notification.smart"},
        {"step": 5, "action": "await_approval",
         "detail": "attende approvazione umana per l'esecuzione"},
    ]
    return plan


def run(**kwargs):
    action = kwargs.get("action") or "handle"
    params = kwargs.get("params") or {}
    try:
        if action == "list_mappings":
            return {"ok": True, "action": "list_mappings",
                    "mappings": DIAGNOSIS_MAPPINGS}

        payload = params.get("alert_payload") or {}
        if not payload:
            return {"ok": False, "error": "alert_payload richiesto"}

        context = _extract_context(payload)

        if action == "parse":
            return {"ok": True, "action": "parse", "context": context}

        if action == "handle":
            auto_diagnose = params.get("auto_diagnose", True)
            auto_remediate = params.get("auto_remediate", False)
            diagnosis_tools = _select_diagnosis_tools(context)
            plan = _build_remediation_plan(context, diagnosis_tools)
            return {
                "ok": True,
                "action": "handle",
                "context": context,
                "diagnosis": {
                    "tools_selected": diagnosis_tools,
                    "auto_diagnose": auto_diagnose,
                    "note": "i tool vanno eseguiti dall'agente (con approvazione se WRITE)",
                },
                "remediation_plan": plan,
                "auto_remediate": auto_remediate,
                "status": "ready_for_diagnosis",
            }

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "monitor.alert_to_agent", "doc": "Processa il payload di un alert Azure Monitor e avvia la diagnosi automatica: estrae il contesto (risorsa, metrica, severità), seleziona i tool di diagnosi pertinenti, esegue l'analisi e propone una remediation. Args: {\"action\": \"handle\"|\"parse\"|\"list_mappings\", \"params\": {\"alert_payload\": {} (opz), \"auto_diagnose\": bool (opz, default true), \"auto_remediate\": bool (opz, default false)}}. Ritorna: {context, diagnosis: [...], remediation_plan: [...], status}.", "write": False, "entrypoint": "run"}
    ]
}
