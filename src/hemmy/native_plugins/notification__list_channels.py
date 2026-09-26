# Plugin auto-generato per il tool 'notification.list_channels'.
# Installato via meta.install_tool con approvazione umana.

import os


def _secret(name):
    # Segreto per-utente (Impostazioni), fallback a env var solo per CLI
    # locale senza portale (vedi auth.user_context).
    try:
        from hemmy.auth.user_context import get_current_user_secret_or_env

        return get_current_user_secret_or_env(name)
    except Exception:
        return os.environ.get(name.upper())


def run(**kwargs):
    channels = []

    tg_token = _secret('telegram_bot_token')
    tg_chat = _secret('telegram_chat_id')
    channels.append({
        'channel': 'telegram',
        'configured': bool(tg_token and tg_chat),
        'has_bot_token': bool(tg_token),
        'has_chat_id': bool(tg_chat),
    })

    smtp_host = _secret('smtp_host')
    smtp_user = _secret('smtp_user')
    smtp_pass = _secret('smtp_password')
    channels.append({
        'channel': 'email',
        'configured': bool(smtp_host and smtp_user and smtp_pass),
        'has_host': bool(smtp_host),
        'has_user': bool(smtp_user),
        'has_password': bool(smtp_pass),
    })

    webhook = _secret('webhook_url')
    channels.append({
        'channel': 'webhook',
        'configured': bool(webhook),
        'has_url': bool(webhook),
    })

    configured = [c['channel'] for c in channels if c['configured']]
    return {'ok': True, 'configured_channels': configured, 'channels': channels}

MANIFEST = {
    "tools": [
        {"name": "notification.list_channels", "doc": "[READ] Elenca i canali di notifica configurati (Telegram, webhook, email) verificando la presenza delle credenziali per-utente nelle Impostazioni. Args: {}. Ritorna canali disponibili e stato configurazione.", "write": False, "entrypoint": "run"}
    ]
}
