"""Classificazione del rischio delle remediation (il layer "diagnosi → azione").

Quando l'agente diagnostica un errore e individua una correzione, deve sapere *quanto
è rischiosa* prima di proporla/eseguirla. Questo modulo è una knowledge base
DETERMINISTICA (nessuna chiamata ad Azure) che, dato un tipo di remediation, ritorna:
- risk: low | medium | high
- policy operativa: se procedere con la sola approvazione standard oppure richiedere
  una conferma esplicita dell'impatto,
- rationale + eventuale alternativa più sicura.

Modello enterprise (coerente con l'human-in-the-loop del progetto):
  low    → diagnosi autonoma + una fix proposta, approvazione standard sulle scritture.
  medium → proponi la fix e spiega l'impatto; approvazione standard.
  high   → richiedi CONFERMA ESPLICITA dell'impatto prima dell'approvazione; preferisci
           l'alternativa più sicura se esiste.
"""

from __future__ import annotations

from typing import Any

# type_remediation -> (risk, rationale, safer_alternative|None)
_KB: dict[str, dict[str, Any]] = {
    # --- LOW: correzioni di codice/state senza perdita dati ---
    "moved_block": {
        "risk": "low",
        "rationale": "Rinomina l'indirizzo nello state (moved {}): nessuna distruzione, nessuna perdita di dati.",
    },
    "for_each_refactor": {
        "risk": "low",
        "rationale": "Correzione di codice (count→for_each) per un valore known-after-apply: nessun effetto su risorse esistenti.",
    },
    "add_providers_block": {
        "risk": "low",
        "rationale": "Aggiunge required_providers/required_version: correzione di configurazione, inerte sulle risorse.",
    },
    "add_backend_config": {
        "risk": "low",
        "rationale": "Configura il backend/-backend-config: non modifica risorse Azure.",
    },
    "commit_fix": {
        "risk": "low",
        "rationale": "Correzione del contenuto di un file .tf (es. placeholder, sintassi) prima del deploy.",
    },
    "sku_change": {
        "risk": "low",
        "rationale": "Cambio di SKU che non forza ricreazione (es. SQL DB S0): update in-place.",
    },
    # --- MEDIUM: riconciliazione/modifiche reversibili con impatto contenuto ---
    "import_resource": {
        "risk": "medium",
        "rationale": "Importa una risorsa esistente nello state (riconciliazione): non modifica la risorsa, ma un blocco .tf disallineato può poi generare update inattesi.",
    },
    "break_lease": {
        "risk": "medium",
        "rationale": "Rilascia un lease orfano sul blob di state: sblocca la scrittura, ma va fatto solo se nessuna run sta davvero scrivendo.",
    },
    "network_acl_change": {
        "risk": "medium",
        "rationale": "Modifica network rules/firewall: può interrompere connettività legittima se troppo restrittiva o esporre se troppo aperta.",
    },
    "set_secret": {
        "risk": "medium",
        "rationale": "Crea/aggiorna un segreto (KV o Actions): impatta chi lo consuma; il valore resta fuori dall'LLM.",
    },
    "trigger_apply_no_destroy": {
        "risk": "medium",
        "rationale": "Apply di un piano con 0 to destroy: crea/aggiorna risorse, nessuna distruzione.",
    },
    # --- HIGH: distruttive/irreversibili o che cambiano i privilegi ---
    "state_rm": {
        "risk": "high",
        "rationale": "Rimuove risorse dallo state: rischio di orfani e di ricreazione/distruzione al prossimo apply.",
        "safer_alternative": "Per un refactor di indirizzo usa un blocco moved {} (moved_block), non state rm.",
    },
    "destroy_recreate": {
        "risk": "high",
        "rationale": "Distrugge e ricrea la risorsa: perdita di dati/downtime; per alcune risorse la destroy si impianta.",
        "safer_alternative": "Se è solo un cambio di indirizzo, usa moved {}; se è un cambio in-place, evita il replace.",
    },
    "apply_with_destroy": {
        "risk": "high",
        "rationale": "Apply di un piano che DISTRUGGE risorse: potenziale perdita di dati.",
        "safer_alternative": "Verifica il destroy: se non è voluto, correggi il codice (spesso moved {}). Procedi solo con confirm_destroy esplicito.",
    },
    "rbac_assign": {
        "risk": "high",
        "rationale": "Assegna un ruolo RBAC: cambia i privilegi di un principal (potenziale escalation).",
    },
    "move_resources": {
        "risk": "high",
        "rationale": "Sposta risorse tra resource group: rompe Private Endpoint/DNS/Managed VNet, richiede ricreazione.",
    },
    "delete_resource": {
        "risk": "high",
        "rationale": "Elimina una risorsa: irreversibile, possibili dipendenze a valle.",
    },
    "force_unlock": {
        "risk": "high",
        "rationale": "Force-unlock dello state Terraform: se una run sta davvero scrivendo, corrompe lo state.",
        "safer_alternative": "Verifica che nessuna run sia attiva; preferisci il break-lease del blob se il lock è orfano.",
    },
}

_ALIASES = {
    "moved": "moved_block",
    "move": "moved_block",
    "for_each": "for_each_refactor",
    "foreach": "for_each_refactor",
    "count_to_for_each": "for_each_refactor",
    "providers": "add_providers_block",
    "backend": "add_backend_config",
    "lint_fix": "commit_fix",
    "fix_file": "commit_fix",
    "import": "import_resource",
    "lease": "break_lease",
    "break_lease_blob": "break_lease",
    "firewall": "network_acl_change",
    "network_rules": "network_acl_change",
    "secret": "set_secret",
    "rm": "state_rm",
    "state_remove": "state_rm",
    "replace": "destroy_recreate",
    "recreate": "destroy_recreate",
    "apply_destroy": "apply_with_destroy",
    "rbac": "rbac_assign",
    "assign_role": "rbac_assign",
    "move_rg": "move_resources",
    "delete": "delete_resource",
    "unlock": "force_unlock",
}

_POLICY = {
    "low": {
        "auto_apply_allowed": True,
        "requires_explicit_confirmation": False,
        "policy": "Diagnosi autonoma: proponi UNA fix e procedi con l'approvazione standard sulle scritture.",
    },
    "medium": {
        "auto_apply_allowed": False,
        "requires_explicit_confirmation": False,
        "policy": "Proponi la fix spiegando l'impatto; procedi con l'approvazione standard.",
    },
    "high": {
        "auto_apply_allowed": False,
        "requires_explicit_confirmation": True,
        "policy": "Richiedi CONFERMA ESPLICITA dell'impatto prima dell'approvazione; se esiste, preferisci l'alternativa più sicura.",
    },
}


def _canon(remediation: str) -> str:
    r = (remediation or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _ALIASES.get(r, r)


def classify(remediation: str, error: str | None = None) -> dict[str, Any]:
    """Classifica il rischio di una remediation e indica come procedere.

    remediation: identificatore della correzione (es. 'moved_block', 'state_rm',
        'apply_with_destroy', 'rbac_assign', ...). Accetta sinonimi comuni.
    error: (opz.) testo dell'errore diagnosticato, riportato nell'output per contesto.

    Ritorna {remediation, canonical, known, risk, auto_apply_allowed,
    requires_explicit_confirmation, policy, rationale, safer_alternative?}.
    Tipi sconosciuti → rischio 'medium' prudenziale (non auto-applicabile).
    """
    key = _canon(remediation)
    entry = _KB.get(key)
    if entry is None:
        pol = _POLICY["medium"]
        return {
            "remediation": remediation,
            "canonical": key,
            "known": False,
            "risk": "medium",
            **pol,
            "rationale": "Remediation non in knowledge base: default prudenziale 'medium'. "
            "Valuta manualmente reversibilità e impatto prima di procedere.",
            "error": error,
        }
    risk = entry["risk"]
    pol = _POLICY[risk]
    out = {
        "remediation": remediation,
        "canonical": key,
        "known": True,
        "risk": risk,
        **pol,
        "rationale": entry["rationale"],
        "error": error,
    }
    if entry.get("safer_alternative"):
        out["safer_alternative"] = entry["safer_alternative"]
    return out
