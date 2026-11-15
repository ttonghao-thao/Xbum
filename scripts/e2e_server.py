#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)
with tempfile.TemporaryDirectory(prefix="xbum-e2e-") as temp:
    env = {**os.environ, "DATA_DIR": temp, "DATABASE_URL": f"sqlite:///{temp}/album.sqlite3"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
    subprocess.run(
        [sys.executable, "scripts/create_sample_library.py", "--count", "4", "--output", "runtime/e2e-samples"],
        check=True,
    )
    worker = subprocess.Popen([sys.executable, "-m", "app.workers.worker"], env=env)
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8001", "--no-access-log"],
        env=env,
    )
    try:
        process.wait()
    except KeyboardInterrupt:
        pass
    finally:
        process.terminate()
        worker.terminate()
        process.wait(timeout=10)
        worker.wait(timeout=10)
