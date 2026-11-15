#!/usr/bin/env python3
import argparse
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
STOP_TIMEOUT = 30


@contextmanager
def locked(name, *, blocking=True):
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    with open(RUNTIME / name, "a+") as handle:
        os.chmod(handle.name, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise RuntimeError("Xbum 已在运行；请使用 make restart，或先执行 make stop") from None
        yield handle


def is_alive(process):
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def matches_supervisor(process, *, legacy_only=False):
    """Match an actual Python script invocation in this checkout, never a shell/grep string."""
    if process.pid == os.getpid():
        return False
    try:
        args = process.cmdline()
        cwd = Path(process.cwd()).resolve()
        if cwd != ROOT or not args or not Path(args[0]).name.lower().startswith("python"):
            return False
        arguments = args[1:]
        while arguments and arguments[0] in {"-u", "-B"}:
            arguments = arguments[1:]
        if not arguments or (cwd / arguments[0]).resolve() != ROOT / "scripts/run.py":
            return False
        tail = [arg for arg in arguments[1:] if arg != "--dev"]
        if legacy_only:
            return not tail
        return tail in ([], ["start"], ["restart"])
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False


def supervisors():
    found = {}
    try:
        state = json.loads((RUNTIME / "run.lock").read_text())
        process = psutil.Process(state["pid"])
        if process.create_time() == state["created"] and matches_supervisor(process):
            found[process.pid] = process
    except (OSError, ValueError, KeyError, TypeError, psutil.NoSuchProcess):
        pass
    # Before lifecycle commands existed, run.py had no PID record. Limit fallback
    # discovery to its exact invocation and cwd; new start/restart use the locked record.
    for process in psutil.process_iter():
        if matches_supervisor(process, legacy_only=True):
            found[process.pid] = process
    return list(found.values())


def descendants(processes):
    found = {p.pid: p for p in processes}
    for process in processes:
        try:
            found.update((p.pid, p) for p in process.children(recursive=True))
        except psutil.NoSuchProcess:
            pass
    return list(found.values())


def wait_until_stopped(processes, timeout):
    deadline = time.monotonic() + timeout
    while True:
        remaining = [p for p in processes if is_alive(p)]
        if not remaining or time.monotonic() >= deadline:
            return remaining
        time.sleep(0.1)


def stop():
    running = supervisors()
    if not running:
        print("Xbum 未运行，无需停止。", flush=True)
        return
    owned = descendants(running)
    print("正在停止 Xbum 及其启动的服务…", flush=True)
    for process in running:
        try:
            process.terminate()  # psutil checks PID reuse before sending the signal.
        except psutil.NoSuchProcess:
            pass
    remaining = wait_until_stopped(owned, STOP_TIMEOUT)
    if remaining:
        pids = ", ".join(str(p.pid) for p in remaining)
        raise RuntimeError(f"停止超时，仍有本次服务进程（PID {pids}）；未启动新实例，请检查原终端日志")
    print("Xbum 已停止。独立启动的 Ollama 保留。", flush=True)


def shutdown(children):
    active = []
    for child in children:
        if child.poll() is None:
            try:
                active.append(psutil.Process(child.pid))
            except psutil.NoSuchProcess:
                pass
    owned = descendants(active)
    # Each direct child has its own process group, including npm's Vite child.
    for child in reversed(children):
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 10
    for child in children:
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    # Cover subprocesses that opened a separate session (e.g. model runners).
    for process in owned:
        if is_alive(process):
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
    for child in children:
        child.wait(timeout=5)
    if wait_until_stopped(owned, 5):
        raise RuntimeError("部分子进程未能退出，请检查原终端日志")


def supervise(settings, dev):
    children = []
    endpoint = urlparse(settings.ollama_base_url)
    env = {
        **os.environ,
        "OLLAMA_NO_CLOUD": "1",
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        "OLLAMA_KEEP_ALIVE": "0",
        "OLLAMA_HOST": endpoint.netloc,
        "APP_PORT": str(settings.app_port),
    }
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        migration = subprocess.Popen([sys.executable, "-m", "alembic", "upgrade", "head"], start_new_session=True)
        children.append(migration)
        if migration.wait() != 0:
            raise RuntimeError("数据库迁移失败，未启动服务；请检查上面的错误")
        children.remove(migration)
        try:
            httpx.get(settings.ollama_base_url + "/api/version", timeout=1, trust_env=False).raise_for_status()
            print("复用独立运行的 Ollama；停止 Xbum 时会保留它。", flush=True)
        except httpx.HTTPError:
            if shutil.which("ollama"):
                children.append(subprocess.Popen(["ollama", "serve"], env=env, start_new_session=True))
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
                start_new_session=True,
            )
        )
        children.append(subprocess.Popen([sys.executable, "-m", "app.workers.worker"], env=env, start_new_session=True))
        if dev:
            children.append(
                subprocess.Popen(
                    ["npm", "run", "dev", "--", "--port", str(settings.web_port)],
                    cwd=ROOT / "apps/web",
                    env=env,
                    start_new_session=True,
                )
            )
        print(f"Xbum: http://{settings.app_host}:{settings.web_port if dev else settings.app_port}/setup", flush=True)
        print("Ctrl+C 或另一终端 make stop 停止；make restart 停止后重新启动。", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise RuntimeError("子进程退出，请检查上面的错误；其他进程已停止")
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        shutdown(children)


def main():
    parser = argparse.ArgumentParser(description="启动、停止或重启当前仓库的 Xbum 服务")
    parser.add_argument("command", nargs="?", choices=["start", "stop", "restart"], default="start")
    parser.add_argument("--dev", action="store_true", help="启动 Vite 开发服务器")
    args = parser.parse_args()
    if args.command == "stop" and args.dev:
        parser.error("stop 会停止当前实例的全部进程，无需 --dev")
    os.chdir(ROOT)
    try:
        with ExitStack() as stack:
            # Serialize control operations, but release this lock while serving.
            with locked("control.lock"):
                if args.command in {"stop", "restart"}:
                    stop()
                if args.command == "stop":
                    return
                handle = stack.enter_context(locked("run.lock", blocking=False))
                if supervisors():
                    raise RuntimeError("旧版 Xbum 已在运行；请使用 make restart，或先执行 make stop")
                handle.seek(0)
                handle.truncate()
                json.dump({"pid": os.getpid(), "created": psutil.Process().create_time()}, handle)
                handle.flush()
            # stop does not depend on .env validity or database/model availability.
            from app.core.config import settings

            supervise(settings, args.dev)
    except (RuntimeError, psutil.Error) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
