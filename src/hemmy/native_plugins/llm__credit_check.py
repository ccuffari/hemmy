# Plugin auto-generato per il tool 'llm.credit_check'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error


DEEPSEEK_BALANCE_URL = 'https://api.deepseek.com/user/balance'


def _http_get(url, headers):
    req = urllib.request.Request(url, method='GET')
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode()), None
    except urllib.error.HTTPError as e:
        return None, {'status': e.code, 'detail': e.read().decode()[:300]}
    except Exception as e:
        return None, {'error': str(e)}


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


def _deepseek_balance(api_key):
    data, err = _http_get(DEEPSEEK_BALANCE_URL, {'Authorization': 'Bearer ' + api_key, 'Accept': 'application/json'})
    if err:
        return None, err
    infos = data.get('balance_infos') or []
    if not infos:
        return {'is_available': data.get('is_available'), 'balance_infos': []}, None
    return {'is_available': data.get('is_available'), 'balance_infos': infos}, None


def _secret(name):
    # Segreto per-utente (Impostazioni), fallback a env var solo per CLI
    # locale senza portale (vedi auth.user_context).
    try:
        from hemmy.auth.user_context import get_current_user_secret_or_env

        return get_current_user_secret_or_env(name)
    except Exception:
        return os.environ.get(name.upper())


def _llm_api_key(provider):
    # Stessa convenzione BYOK usata da interfaces/web.py: chiave specifica per
    # provider ('llm_api_key__<slug>'), altrimenti la chiave generica
    # 'llm_api_key' (usata quando il provider è quello primario).
    key = _secret(f"llm_api_key__{provider}") or _secret("llm_api_key")
    if key:
        return key
    # Fallback SOLO per CLI locale senza portale (nessun utente legato al
    # thread): env var storica DEEPSEEK_API_KEY, per compatibilità.
    try:
        from hemmy.auth.user_context import get_current_user_id

        if get_current_user_id() is not None:
            return None
    except Exception:
        pass
    return os.environ.get("DEEPSEEK_API_KEY")


def _send_telegram(token, chat_id, text):
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    return _http_post(url, {'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'})


def run(**kwargs):
    provider = (kwargs.get('provider') or 'deepseek').lower()
    threshold = float(kwargs.get('threshold', 5.0))
    notify = bool(kwargs.get('notify', True))
    currency = kwargs.get('currency', 'USD')

    if provider != 'deepseek':
        return {'ok': False, 'error': f'provider non supportato: {provider}. Supportati: deepseek'}

    api_key = _llm_api_key(provider)
    if not api_key:
        return {'ok': False, 'error': "credenziale 'llm_api_key' (o 'llm_api_key__deepseek') non configurata nelle Impostazioni"}

    bal, err = _deepseek_balance(api_key)
    if err:
        return {'ok': False, 'provider': provider, 'error': err}

    infos = bal.get('balance_infos') or []
    total = None
    for info in infos:
        if (info.get('currency') or '').upper() == currency.upper():
            total = info.get('total_balance')
            break
    if total is None and infos:
        total = infos[0].get('total_balance')
        currency = infos[0].get('currency', currency)

    try:
        total_f = float(total) if total is not None else None
    except (TypeError, ValueError):
        total_f = None

    alert = (total_f is not None and total_f < threshold)
    result = {
        'ok': True,
        'provider': provider,
        'is_available': bal.get('is_available'),
        'balance_infos': infos,
        'remaining': total_f,
        'currency': currency,
        'threshold': threshold,
        'alert': alert,
    }

    if alert and notify:
        token = _secret('telegram_bot_token')
        chat_id = _secret('telegram_chat_id')
        if not token or not chat_id:
            result['notify'] = {'sent': False, 'error': "credenziali 'telegram_bot_token'/'telegram_chat_id' mancanti nelle Impostazioni"}
        else:
            text = (f"\u26a0\ufe0f *DeepSeek credit low*\n"
                    f"Saldo: *{total_f} {currency}*\n"
                    f"Soglia: {threshold} {currency}\n"
                    f"Ricarica il credito per evitare interruzioni.")
            resp, terr = _send_telegram(token, chat_id, text)
            result['notify'] = {'sent': terr is None, 'response': resp, 'error': terr}
    else:
        result['notify'] = {'sent': False, 'reason': 'no alert o notify=false'}

    return result

MANIFEST = {
    "tools": [
        {"name": "llm.credit_check", "doc": "[READ] Verifica il credito residuo DeepSeek via API key (credenziale per-utente 'llm_api_key'/'llm_api_key__deepseek' nelle Impostazioni) e, se sotto soglia, invia una notifica Telegram (credenziali 'telegram_bot_token'/'telegram_chat_id'). Args: {\"provider\": \"deepseek\" (default), \"threshold\": float (opz, default 5.0), \"notify\": bool (opz, default true), \"currency\": str (opz, default USD)}. Ritorna saldo, usage e alert.", "write": False, "entrypoint": "run"}
    ]
}
