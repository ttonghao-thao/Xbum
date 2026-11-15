#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -z "${1:-}" ]]; then echo 'Usage: scripts/restore.sh /absolute/path/backup.tar.gz [--confirm-restore]'; exit 1; fi
exec .venv/bin/python scripts/backup_restore.py restore "$@"
