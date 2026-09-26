# Plugin auto-generato per il tool 'notification.send'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import smtplib
import urllib.request
import urllib.error
from email.mime.text import MIMEText


def _secret(name):
    # Segreto per-utente (Impostazioni), fallback a env var solo per CLI
    # locale senza portale (vedi auth.user_context).
    try:
        from hemmy.auth.user_context import get_current_user_secret_or_env

        return get_current_user_secret_or_env(name)
    except Exception:
        return os.environ.get(name.upper())


def _http_post(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode()), None
    except urllib.error.HTTPError as e:
        return None, {'status': e.code, 'detail': e.read().decode()[:300]}
    except Exception as e:
        return None, {'error': str(e)}


def _send_telegram(message):
    token = _secret('telegram_bot_token')
    chat_id = _secret('telegram_chat_id')
    if not token or not chat_id:
        return {'sent': False, 'error': "credenziali 'telegram_bot_token'/'telegram_chat_id' mancanti nelle Impostazioni"}
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    resp, err = _http_post(url, {'chat_id': chat_id, 'text': message, 'parse_mode': 'Markdown'})
    return {'sent': err is None, 'response': resp, 'error': err}


def _send_webhook(message, webhook_url):
    url = webhook_url or _secret('webhook_url')
    if not url:
        return {'sent': False, 'error': "webhook_url non fornito e credenziale 'webhook_url' non configurata nelle Impostazioni"}
    resp, err = _http_post(url, {'text': message})
    return {'sent': err is None, 'response': resp, 'error': err}


def _send_email(message, subject, to):
    host = _secret('smtp_host')
    port = int(_secret('smtp_port') or '587')
    user = _secret('smtp_user')
    password = _secret('smtp_password')
    sender = _secret('smtp_from') or user
    recipient = to or _secret('smtp_to')
    if not all([host, user, password, recipient]):
        return {'sent': False, 'error': "config SMTP incompleta (credenziali 'smtp_host'/'smtp_user'/'smtp_password'/'smtp_to' nelle Impostazioni)"}
    msg = MIMEText(message)
    msg['Subject'] = subject or 'Notifica agente'
    msg['From'] = sender
    msg['To'] = recipient
    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, password)
            s.sendmail(sender, [recipient], msg.as_string())
        return {'sent': True, 'to': recipient}
    except Exception as e:
        return {'sent': False, 'error': str(e)}


def run(**kwargs):
    message = kwargs.get('message')
    if not message:
        return {'ok': False, 'error': 'parametro message obbligatorio'}
    channel = (kwargs.get('channel') or 'telegram').lower()
    subject = kwargs.get('subject')
    webhook_url = kwargs.get('webhook_url')
    to = kwargs.get('to')

    if channel == 'telegram':
        res = _send_telegram(message)
    elif channel == 'webhook':
        res = _send_webhook(message, webhook_url)
    elif channel == 'email':
        res = _send_email(message, subject, to)
    else:
        return {'ok': False, 'error': f'canale non supportato: {channel}. Usa telegram|webhook|email'}

    return {'ok': res.get('sent', False), 'channel': channel, 'result': res}

MANIFEST = {
    "tools": [
        {"name": "notification.send", "doc": "[WRITE] Invia una notifica su uno o più canali (Telegram, webhook generico, email SMTP). Riusabile da qualsiasi tool per alert. Args: {\"message\": str, \"channel\": \"telegram\"|\"webhook\"|\"email\" (opz, default 'telegram'), \"subject\": str (opz, per email), \"webhook_url\": str (opz, per webhook), \"to\": str (opz, per email)}. Credenziali per-utente configurate nelle Impostazioni ('telegram_bot_token'/'telegram_chat_id', 'smtp_*', 'webhook_url').", "write": True, "entrypoint": "run"}
    ]
}
