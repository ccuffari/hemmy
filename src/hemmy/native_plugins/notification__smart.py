# Plugin auto-generato per il tool 'notification.smart'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import hashlib
import time
import urllib.request
import urllib.error


_SENT_CACHE = {}

ROUTING_RULES = {
    "critical": ["telegram", "webhook", "email"],
    "high": ["telegram", "webhook"],
    "medium": ["telegram"],
    "low": ["telegram"],
}


def _secret(name):
    # Segreto per-utente (Impostazioni), fallback a env var solo per CLI
    # locale senza portale (vedi auth.user_context).
    try:
        from hemmy.auth.user_context import get_current_user_secret_or_env

        return get_current_user_secret_or_env(name)
    except Exception:
        return os.environ.get(name.upper())


def _send_telegram(message):
    token = _secret("telegram_bot_token")
    chat = _secret("telegram_chat_id")
    if not (token and chat):
        return {"channel": "telegram", "sent": False, "error": "credenziali 'telegram_*' mancanti nelle Impostazioni"}
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({"chat_id": chat, "text": message}).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return {"channel": "telegram", "sent": r.status < 300}
    except Exception as e:
        return {"channel": "telegram", "sent": False, "error": str(e)}


def _send_webhook(message, url=None):
    url = url or _secret("webhook_url")
    if not url:
        return {"channel": "webhook", "sent": False, "error": "credenziale 'webhook_url' mancante nelle Impostazioni"}
    data = json.dumps({"text": message}).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return {"channel": "webhook", "sent": r.status < 300}
    except Exception as e:
        return {"channel": "webhook", "sent": False, "error": str(e)}


def _send_email(message, subject=None):
    import smtplib
    from email.mime.text import MIMEText
    host = _secret("smtp_host")
    port = int(_secret("smtp_port") or "587")
    user = _secret("smtp_user")
    pwd = _secret("smtp_password")
    to = _secret("smtp_to")
    if not (host and user and pwd and to):
        return {"channel": "email", "sent": False, "error": "credenziali 'smtp_*' mancanti nelle Impostazioni"}
    try:
        msg = MIMEText(message)
        msg["Subject"] = subject or "Azure Alert"
        msg["From"] = user
        msg["To"] = to
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls()
            s.login(user, pwd)
            s.send_message(msg)
        return {"channel": "email", "sent": True}
    except Exception as e:
        return {"channel": "email", "sent": False, "error": str(e)}


def _dedup_key(message, severity, source):
    raw = f"{severity}|{source}|{message}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def run(**kwargs):
    action = kwargs.get("action") or "send"
    params = kwargs.get("params") or {}
    try:
        if action == "list_rules":
            return {"ok": True, "action": "list_rules", "rules": ROUTING_RULES}

        message = params.get("message")
        severity = params.get("severity") or "medium"
        source = params.get("source") or "agent"
        if not message:
            return {"ok": False, "error": "message richiesto"}

        if action == "route":
            channels = ROUTING_RULES.get(severity, ["telegram"])
            return {"ok": True, "action": "route", "severity": severity,
                    "routed_to": channels}

        if action == "send":
            key = _dedup_key(message, severity, source)
            now = time.time()
            last = _SENT_CACHE.get(key)
            if last and (now - last) < 300:
                return {"ok": True, "action": "send", "sent": False,
                        "deduplicated": True, "severity": severity,
                        "note": "messaggio identico inviato <5 min fa"}

            channels = ROUTING_RULES.get(severity, ["telegram"])
            results = []
            for ch in channels:
                if ch == "telegram":
                    results.append(_send_telegram(f"[{severity.upper()}] {message}"))
                elif ch == "webhook":
                    results.append(_send_webhook(f"[{severity.upper()}] {message}"))
                elif ch == "email":
                    results.append(_send_email(message, subject=f"[{severity.upper()}] Azure Alert"))
            _SENT_CACHE[key] = now
            sent_any = any(r.get("sent") for r in results)
            return {"ok": True, "action": "send", "sent": sent_any,
                    "deduplicated": False, "severity": severity,
                    "routed_to": channels, "results": results}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "notification.smart", "doc": "Notifiche intelligenti con routing per severità/on-call: instrada gli alert al canale giusto (Telegram/webhook/email) in base a severità, deduplica, e applica rate limiting. Usa i canali configurati come credenziali per-utente nelle Impostazioni. Args: {\"action\": \"send\"|\"route\"|\"list_rules\", \"params\": {\"message\": str, \"severity\": \"critical\"|\"high\"|\"medium\"|\"low\" (opz, default 'medium'), \"source\": str (opz)}}. Ritorna: {routed_to: [...], sent: bool, deduplicated: bool}.", "write": True, "entrypoint": "run"}
    ]
}
