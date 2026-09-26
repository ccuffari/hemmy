# Plugin auto-generato per il tool 'storage.get_blob_properties'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import urllib.request
import urllib.error
from hemmy.utils.azure_auth import get_arm_token, get_subscription_id


def _get_token():
    return get_arm_token()
def _get(url, token):
    req = urllib.request.Request(url, method='GET')
    req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def run(**kwargs):
    resource_group = kwargs.get('resource_group')
    account_name = kwargs.get('account_name')
    container = kwargs.get('container')
    blob_name = kwargs.get('blob_name')
    if not all([resource_group, account_name, container, blob_name]):
        return {'ok': False, 'error': 'resource_group, account_name, container, blob_name obbligatori'}
    sub = get_subscription_id()
    if not sub:
        return {'ok': False, 'error': 'subscription non risolvibile'}
    token = _get_token()
    if not token:
        return {'ok': False, 'error': 'impossibile ottenere token Azure'}

    url = (f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{resource_group}'
           f'/providers/Microsoft.Storage/storageAccounts/{account_name}'
           f'/blobServices/default/containers/{container}/blobs/{blob_name}?api-version=2023-01-01')
    try:
        data = _get(url, token)
        props = data.get('properties', {})
        return {
            'ok': True,
            'blob': blob_name,
            'container': container,
            'content_length': props.get('contentLength'),
            'content_type': props.get('contentType'),
            'blob_type': props.get('blobType'),
            'access_tier': props.get('accessTier'),
            'last_modified': props.get('lastModified'),
            'creation_time': props.get('creationTime'),
            'metadata': props.get('metadata'),
            'lease_status': props.get('leaseStatus'),
        }
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': {'status': e.code, 'detail': e.read().decode()[:500]}}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "storage.get_blob_properties", "doc": "[READ] Legge le proprietà di un blob (dimensione, content-type, tier, last-modified, metadata) via ARM REST. Args: {\"resource_group\": str, \"account_name\": str, \"container\": str, \"blob_name\": str}. Ritorna proprietà del blob.", "write": False, "entrypoint": "run"}
    ]
}
