# Plugin auto-generato per il tool 'llm.usage_stats'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error


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
    # 'llm_api_key'.
    key = _secret(f"llm_api_key__{provider}") or _secret("llm_api_key")
    if key:
        return key
    try:
        from hemmy.auth.user_context import get_current_user_id

        if get_current_user_id() is not None:
            return None
    except Exception:
        pass
    return os.environ.get("DEEPSEEK_API_KEY")


def run(**kwargs):
    provider = kwargs.get('provider', 'deepseek')
    if provider != 'deepseek':
        return {'ok': False, 'error': f'provider non supportato: {provider}'}
    api_key = _llm_api_key(provider)
    if not api_key:
        return {'ok': False, 'error': "credenziale 'llm_api_key' (o 'llm_api_key__deepseek') non configurata nelle Impostazioni"}

    url = 'https://api.deepseek.com/user/balance'
    req = urllib.request.Request(url, method='GET')
    req.add_header('Authorization', 'Bearer ' + api_key)
    req.add_header('Accept', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        infos = data.get('balance_infos', [])
        return {
            'ok': True,
            'provider': provider,
            'is_available': data.get('is_available'),
            'balance_infos': [{'currency': i.get('currency'),
                               'total_balance': i.get('total_balance'),
                               'granted_balance': i.get('granted_balance'),
                               'topped_up_balance': i.get('topped_up_balance')}
                              for i in infos],
        }
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:300]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "llm.usage_stats", "doc": "[READ] Legge statistiche di utilizzo/costo del provider LLM (DeepSeek) via API, usando la credenziale per-utente 'llm_api_key'/'llm_api_key__deepseek' configurata nelle Impostazioni. Args: {\"provider\": \"deepseek\" (default)}. Ritorna saldo, usage e stato.", "write": False, "entrypoint": "run"}
    ]
}
