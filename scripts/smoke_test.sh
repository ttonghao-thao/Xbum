#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python - <<'PY'
import httpx
with httpx.Client(base_url='http://127.0.0.1:8000',trust_env=False) as client:
    response=client.get('/api/v1/system/health'); response.raise_for_status()
    assert response.json()['status']=='ok'
    response=client.get('/'); response.raise_for_status()
    assert 'Xbum' in response.text
    assert client.get('/api/v1/photos').status_code==401
print('Live API + frontend + unauthenticated access smoke passed')
PY
