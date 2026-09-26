# Plugin auto-generato per il tool 'finops.waste'.
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
def _finding(rid, rtype, issue, severity, evidence, rec, saving=0.0):
    return {"resource_id": rid, "resource_type": rtype, "issue": issue,
            "severity": severity, "evidence": evidence, "recommendation": rec,
            "estimated_saving_eur": saving}


def _scan_rg(rg):
    findings = []
    code, resp = _arm("GET", f"/subscriptions/{_sub()}/resourceGroups/{rg}/resources", None, "2021-04-01")
    if code >= 300 or not isinstance(resp, dict):
        return findings, {"error": f"list resources failed: {code}"}
    resources = resp.get("value", [])

    for it in resources:
        t = (it.get("type") or "").lower()
        rid = it.get("id")
        if t == "microsoft.compute/disks":
            c, d = _arm("GET", rid, None, "2023-10-02")
            props = d.get("properties", {}) if isinstance(d, dict) else {}
            if props.get("diskState") == "Unattached":
                size = props.get("diskSizeGB", 0)
                findings.append(_finding(rid, "managed_disk", "unattached", "high",
                                         {"disk_state": "Unattached", "size_gb": size},
                                         {"action": "delete", "note": "disco non collegato a nessuna VM"},
                                         round(size * 0.05, 2)))

    for it in resources:
        t = (it.get("type") or "").lower()
        rid = it.get("id")
        if t == "microsoft.network/publicipaddresses":
            c, d = _arm("GET", rid, None, "2023-09-01")
            props = d.get("properties", {}) if isinstance(d, dict) else {}
            if not props.get("ipConfiguration"):
                findings.append(_finding(rid, "public_ip", "unassociated", "medium",
                                         {"ip_configuration": None},
                                         {"action": "delete", "note": "IP pubblico non associato"},
                                         3.0))

    for it in resources:
        t = (it.get("type") or "").lower()
        rid = it.get("id")
        if t == "microsoft.network/networkinterfaces":
            c, d = _arm("GET", rid, None, "2023-09-01")
            props = d.get("properties", {}) if isinstance(d, dict) else {}
            if not props.get("virtualMachine"):
                findings.append(_finding(rid, "nic", "orphan", "low",
                                         {"virtual_machine": None},
                                         {"action": "delete", "note": "NIC non collegata a VM"},
                                         0.0))

    for it in resources:
        t = (it.get("type") or "").lower()
        rid = it.get("id")
        if t == "microsoft.compute/virtualmachines":
            c, d = _arm("GET", f"{rid}/instanceView", None, "2023-09-01")
            statuses = (d.get("statuses") or []) if isinstance(d, dict) else []
            power = next((s.get("code") for s in statuses if (s.get("code") or "").startswith("PowerState/")), None)
            if power in ("PowerState/deallocated", "PowerState/stopped"):
                findings.append(_finding(rid, "virtual_machine", "stopped_with_disks", "medium",
                                         {"power_state": power},
                                         {"action": "review", "note": "VM spenta ma dischi ancora fatturati"},
                                         0.0))

    for it in resources:
        t = (it.get("type") or "").lower()
        rid = it.get("id")
        if t == "microsoft.storage/storageaccounts":
            c, d = _arm("GET", f"{rid}/blobServices/default/containers", None, "2023-01-01")
            containers = (d.get("value") or []) if isinstance(d, dict) else []
            if not containers:
                findings.append(_finding(rid, "storage_account", "empty", "low",
                                         {"containers": 0},
                                         {"action": "review", "note": "storage senza container"},
                                         0.0))

    return findings, {"resources_scanned": len(resources)}


def run(**kwargs):
    action = kwargs.get("action") or "scan"
    params = kwargs.get("params") or {}
    try:
        rg = params.get("resource_group")
        if action in ("scan", "scan_rg"):
            if not rg:
                return {"ok": False, "error": "resource_group richiesto"}
            findings, meta = _scan_rg(rg)
            total = round(sum(f.get("estimated_saving_eur", 0) for f in findings), 2)
            return {"ok": True, "action": action, "resource_group": rg,
                    "findings": findings, "count": len(findings),
                    "estimated_total_saving_eur": total, "meta": meta}

        if action == "summary":
            if not rg:
                return {"ok": False, "error": "resource_group richiesto"}
            findings, meta = _scan_rg(rg)
            by_sev = {}
            for f in findings:
                by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
            total = round(sum(f.get("estimated_saving_eur", 0) for f in findings), 2)
            return {"ok": True, "action": "summary", "resource_group": rg,
                    "total_findings": len(findings), "by_severity": by_sev,
                    "estimated_total_saving_eur": total, "meta": meta}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "finops.waste", "doc": "Rileva risorse Azure idle/sprecate (waste) per FinOps: dischi orfani, IP pubblici non associati, NIC orfane, VM spente con dischi attivi, storage/DB sottoutilizzati, PE orfani. Usa ARM REST API (autenticazione ereditata dal processo). Args: {\"action\": \"scan\"|\"scan_rg\"|\"summary\", \"params\": {\"resource_group\": str (opz)}}. Ritorna findings normalizzati: {resource_id, resource_type, issue, severity, evidence, recommendation, estimated_saving_eur}.", "write": False, "entrypoint": "run"}
    ]
}
