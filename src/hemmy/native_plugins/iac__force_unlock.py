# Plugin auto-generato per il tool 'iac.force_unlock'.
# Installato via meta.install_tool con approvazione umana.

import subprocess, os


def run(**kwargs):
    lock_id = kwargs.get('lock_id')
    wd = kwargs.get('working_dir', 'environments/dev')
    if not lock_id:
        return {'ok': False, 'error': 'lock_id richiesto'}
    if not os.path.isdir(wd):
        return {'ok': False, 'error': f'working_dir non trovata: {wd}'}
    try:
        p = subprocess.run(['terraform', 'force-unlock', '-force', lock_id], cwd=wd, capture_output=True, text=True, timeout=120)
        return {'ok': p.returncode == 0, 'lock_id': lock_id, 'working_dir': wd, 'returncode': p.returncode, 'stdout': p.stdout[-2000:], 'stderr': p.stderr[-2000:]}
    except FileNotFoundError:
        return {'ok': False, 'error': 'terraform non trovato nel PATH'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "iac.force_unlock", "doc": "[WRITE] Rimuove un lock orfano dallo state Terraform (terraform force-unlock). USA SOLO dopo aver verificato che nessuna run CI/CD sia attiva: un force-unlock durante una scrittura corrompe lo state. Args: {\"lock_id\": str, \"working_dir\": str (opz, default 'environments/dev')}.", "write": True, "entrypoint": "run"}
    ]
}
