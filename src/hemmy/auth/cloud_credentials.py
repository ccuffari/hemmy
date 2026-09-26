"""Credenziali statiche per provider cloud e DevOps (cifrate in user_secrets).

Convenzione nomi segreti:
  aws_access_key_id / aws_secret_access_key / aws_session_token / aws_region
  gcp_service_account_json / gcp_project_id
  azdo_organization / azdo_pat
"""
from __future__ import annotations

import json
from typing import Any


# ------------------------------------------------------------------ AWS

AWS_SECRET_NAMES = (
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "aws_region",
)


def save_aws_credentials(users: Any, user_id: Any, *, access_key_id: str,
                         secret_access_key: str, session_token: str = "",
                         region: str = "eu-west-1") -> None:
    ak = (access_key_id or "").strip()
    sk = (secret_access_key or "").strip()
    if not ak or not sk:
        raise ValueError("access_key_id e secret_access_key sono obbligatori")
    users.set_secret(user_id, "aws_access_key_id", ak)
    users.set_secret(user_id, "aws_secret_access_key", sk)
    users.set_secret(user_id, "aws_session_token", (session_token or "").strip())
    users.set_secret(user_id, "aws_region", (region or "eu-west-1").strip())


def load_aws_credentials(users: Any, user_id: Any) -> dict[str, str] | None:
    try:
        ak = users.get_secret(user_id, "aws_access_key_id")
        sk = users.get_secret(user_id, "aws_secret_access_key")
    except Exception:  # noqa: BLE001
        return None
    if not ak or not sk:
        return None
    out = {"aws_access_key_id": ak, "aws_secret_access_key": sk}
    try:
        st = users.get_secret(user_id, "aws_session_token")
        if st:
            out["aws_session_token"] = st
    except Exception:  # noqa: BLE001
        pass
    try:
        rg = users.get_secret(user_id, "aws_region")
        if rg:
            out["aws_region"] = rg
    except Exception:  # noqa: BLE001
        pass
    return out


def aws_status(users: Any, user_id: Any) -> dict[str, Any]:
    creds = load_aws_credentials(users, user_id)
    if not creds:
        return {"status": "disconnected"}
    ak = creds["aws_access_key_id"]
    masked = ak[:4] + "…" + ak[-4:] if len(ak) > 8 else "••••"
    return {
        "status": "connected",
        "message": masked,
        "region": creds.get("aws_region", ""),
    }


def disconnect_aws(users: Any, user_id: Any) -> None:
    for name in AWS_SECRET_NAMES:
        try:
            users.delete_secret(user_id, name)
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ GCP

GCP_SECRET_NAMES = ("gcp_service_account_json", "gcp_project_id")


def save_gcp_credentials(users: Any, user_id: Any, *, service_account_json: str) -> dict[str, str]:
    raw = (service_account_json or "").strip()
    if not raw:
        raise ValueError("service_account_json obbligatorio")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON non valido: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("il JSON deve essere un oggetto")
    if data.get("type") != "service_account":
        raise ValueError("il JSON non è un service account (campo 'type' != 'service_account')")
    project_id = str(data.get("project_id") or "").strip()
    client_email = str(data.get("client_email") or "").strip()
    if not project_id or not client_email:
        raise ValueError("il JSON non contiene project_id o client_email")
    users.set_secret(user_id, "gcp_service_account_json", raw)
    users.set_secret(user_id, "gcp_project_id", project_id)
    return {"project_id": project_id, "client_email": client_email}


def load_gcp_credentials(users: Any, user_id: Any) -> dict[str, Any] | None:
    try:
        raw = users.get_secret(user_id, "gcp_service_account_json")
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def gcp_status(users: Any, user_id: Any) -> dict[str, Any]:
    data = load_gcp_credentials(users, user_id)
    if not data:
        return {"status": "disconnected"}
    return {
        "status": "connected",
        "message": data.get("client_email", ""),
        "project_id": data.get("project_id", ""),
    }


def disconnect_gcp(users: Any, user_id: Any) -> None:
    for name in GCP_SECRET_NAMES:
        try:
            users.delete_secret(user_id, name)
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ Azure DevOps

AZDO_SECRET_NAMES = ("azdo_organization", "azdo_pat")


def save_azdo_credentials(users: Any, user_id: Any, *, organization: str, pat: str) -> None:
    org = (organization or "").strip()
    org = org.replace("https://", "").replace("http://", "").strip("/")
    pat = (pat or "").strip()
    if not org or not pat:
        raise ValueError("organization e pat sono obbligatori")
    users.set_secret(user_id, "azdo_organization", org)
    users.set_secret(user_id, "azdo_pat", pat)


def load_azdo_credentials(users: Any, user_id: Any) -> dict[str, str] | None:
    try:
        org = users.get_secret(user_id, "azdo_organization")
        pat = users.get_secret(user_id, "azdo_pat")
    except Exception:  # noqa: BLE001
        return None
    if not org or not pat:
        return None
    return {"organization": org, "pat": pat}


def azdo_status(users: Any, user_id: Any) -> dict[str, Any]:
    creds = load_azdo_credentials(users, user_id)
    if not creds:
        return {"status": "disconnected"}
    return {"status": "connected", "message": creds["organization"]}


def disconnect_azdo(users: Any, user_id: Any) -> None:
    for name in AZDO_SECRET_NAMES:
        try:
            users.delete_secret(user_id, name)
        except Exception:  # noqa: BLE001
            pass