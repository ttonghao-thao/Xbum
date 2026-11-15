#!/usr/bin/env python3
import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from app.core.config import settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    children = []
    env = {
        **os.environ,
        "OLLAMA_NO_CLOUD": "1",
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        "OLLAMA_KEEP_ALIVE": "0",
        "OLLAMA_HOST": "127.0.0.1:11434",
        "APP_PORT": str(settings.app_port),
    }
    try:
        try:
            httpx.get(settings.ollama_base_url + "/api/version", timeout=1, trust_env=False).raise_for_status()
        except httpx.HTTPError:
            if shutil.which("ollama"):
                children.append(subprocess.Popen(["ollama", "serve"], env=env))
        children.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    settings.app_host,
                    "--port",
                    str(settings.app_port),
                    "--no-access-log",
                ],
                env=env,
            )
        )
        children.append(subprocess.Popen([sys.executable, "-m", "app.workers.worker"], env=env))
        if args.dev:
            children.append(
                subprocess.Popen(
                    ["npm", "run", "dev", "--", "--port", str(settings.web_port)],
                    cwd=root / "apps/web",
                    env=env,
                )
            )
        print(
            f"Xbum: http://{settings.app_host}:{settings.web_port if args.dev else settings.app_port}/setup", flush=True
        )
        print("Ctrl+C 同时停止本次启动的进程；持久任务会在下次启动安全恢复。", flush=True)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise SystemExit("子进程退出，请检查上面的错误；其他进程已停止")
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == "__main__":
    main()
