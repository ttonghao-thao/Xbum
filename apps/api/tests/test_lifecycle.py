"""Real control commands/API/Worker; temporary data and a stand-in Ollama process only."""

import fcntl
import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil
import pytest

ROOT = Path(__file__).resolve().parents[3]
LEGACY = """import signal, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
Path('legacy-child').write_text(str(child.pid))
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
try:
    while True: time.sleep(.1)
finally:
    child.terminate()
    child.wait(timeout=5)
"""


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def alive(process):
    return process.is_running() and process.status() != psutil.STATUS_ZOMBIE


def eventually(check, *, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.1)
    raise AssertionError("Lifecycle condition timed out")


class Service:
    def __init__(self, root):
        self.root = root
        (root / "scripts").mkdir()
        (root / "apps").mkdir()
        shutil.copy(ROOT / "scripts/run.py", root / "scripts/run.py")
        shutil.copy(ROOT / "Makefile", root / "Makefile")
        shutil.copy(ROOT / "alembic.ini", root / "alembic.ini")
        (root / "apps/api").symlink_to(ROOT / "apps/api", target_is_directory=True)
        (root / "apps/web").symlink_to(ROOT / "apps/web", target_is_directory=True)
        self.port, self.web_port, self.ollama_port = free_port(), free_port(), free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.ollama = f"http://127.0.0.1:{self.ollama_port}"
        self.bin = root / "bin"
        self.bin.mkdir()
        # Never launch the host's Ollama or load real models from lifecycle tests.
        stand_in = self.bin / "ollama"
        stand_in.write_text(
            f"#!{sys.executable}\n"
            + """import os
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"version":"lifecycle-test-only"}')
    def log_message(self, *args): pass
host, port = os.environ['OLLAMA_HOST'].rsplit(':', 1)
HTTPServer((host, int(port)), Handler).serve_forever()
"""
        )
        stand_in.chmod(0o700)
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "DATA_DIR": str(root / "data"),
            "DATABASE_URL": f"sqlite:///{root}/data/test.sqlite3",
            "MODEL_DIR": str(root / "no-models"),
            "APP_HOST": "127.0.0.1",
            "APP_PORT": str(self.port),
            "WEB_PORT": str(self.web_port),
            "OLLAMA_BASE_URL": self.ollama,
            "OLLAMA_HOST": f"127.0.0.1:{self.ollama_port}",
        }
        self.processes = []
        self.log = open(root / "service.log", "w+")

    def command(self, action, *args):
        return ["make", action, f"PY={sys.executable}", *args]

    def launch(self, action="start", *args):
        process = subprocess.Popen(
            self.command(action, *args),
            cwd=self.root,
            env=self.env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.processes.append(process)
        return process

    def run(self, action):
        return subprocess.run(
            self.command(action),
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=45,
        )

    def record(self):
        return json.loads((self.root / "runtime/run.lock").read_text())

    def tree(self):
        parent = psutil.Process(self.record()["pid"])
        return [parent, *parent.children(recursive=True)]

    def ready(self, url=None):
        def healthy():
            try:
                return (
                    httpx.get(url or self.base + "/api/v1/system/health", timeout=0.5, trust_env=False).status_code
                    == 200
                )
            except httpx.HTTPError:
                return False

        eventually(healthy)

    def assert_gone(self, processes):
        eventually(lambda: not any(alive(p) for p in processes))

    def close(self):
        try:
            self.run("stop")
        finally:
            for parent in self.processes:
                if parent.poll() is None:
                    process = psutil.Process(parent.pid)
                    children = process.children(recursive=True)
                    for child in reversed(children):
                        if alive(child):
                            child.kill()
                    parent.kill()
                parent.wait(timeout=5)
            self.log.close()


@pytest.fixture
def service(tmp_path):
    result = Service(tmp_path)
    try:
        yield result
    finally:
        result.close()


def test_make_restart_singleton_and_owned_dev_processes(service):
    original = service.launch()
    service.ready()
    old_tree = service.tree()
    assert any("ollama" in p.cmdline()[1] for p in old_tree if len(p.cmdline()) > 1)
    duplicate = service.run("start")
    assert duplicate.returncode != 0 and "已在运行" in duplicate.stderr
    assert original.poll() is None
    restarted = service.launch("restart", "DEV=1")
    eventually(lambda: service.record()["pid"] != old_tree[0].pid)
    service.ready()
    service.ready(f"http://127.0.0.1:{service.web_port}/api/v1/system/health")
    service.assert_gone(old_tree)
    assert original.wait(timeout=5) == 0
    new_tree = service.tree()
    assert len(new_tree) >= 5  # supervisor, API, Worker, Ollama, npm/Vite
    assert (service.root / "runtime/run.lock").stat().st_mode & 0o777 == 0o600
    stopped = service.run("stop")
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    service.assert_gone(new_tree)
    assert restarted.wait(timeout=5) == 0
    assert "无需停止" in service.run("stop").stdout


def test_external_ollama_survives_stop_and_ctrl_c(service):
    external = subprocess.Popen([str(service.bin / "ollama"), "serve"], env=service.env)
    try:
        service.ready(service.ollama + "/api/version")
        first = service.launch()
        service.ready()
        tree = service.tree()
        assert external.pid not in {p.pid for p in tree}
        # Stopping still works after configuration/data-path edits, even if the
        # new configuration is invalid for starting the application.
        endpoint = service.env["OLLAMA_BASE_URL"]
        service.env["OLLAMA_BASE_URL"] = "https://invalid.example.com"
        service.env["DATA_DIR"] = str(service.root / "changed-data")
        assert service.run("stop").returncode == 0
        service.assert_gone(tree)
        assert first.wait(timeout=5) == 0
        assert external.poll() is None
        service.env["OLLAMA_BASE_URL"] = endpoint
        service.env["DATA_DIR"] = str(service.root / "data")
        second = service.launch()
        service.ready()
        tree = service.tree()
        tree[0].send_signal(signal.SIGINT)
        assert second.wait(timeout=15) == 0
        service.assert_gone(tree)
        assert external.poll() is None
    finally:
        external.terminate()
        external.wait(timeout=5)


def test_legacy_discovery_stale_pid_and_other_checkout_are_safe(service, tmp_path):
    script = service.root / "scripts/run.py"
    current = script.read_text()
    script.write_text(LEGACY)
    legacy = subprocess.Popen([sys.executable, "-u", "scripts/run.py", "--dev"], cwd=service.root)
    other = tmp_path / "other"
    (other / "scripts").mkdir(parents=True)
    (other / "scripts/run.py").write_text(LEGACY)
    neighbour = subprocess.Popen([sys.executable, "scripts/run.py"], cwd=other)
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        eventually(lambda: (service.root / "legacy-child").exists() and (other / "legacy-child").exists())
        script.write_text(current)
        runtime = service.root / "runtime"
        runtime.mkdir()
        stale = {"pid": unrelated.pid, "created": psutil.Process(unrelated.pid).create_time() - 1}
        (runtime / "run.lock").write_text(json.dumps(stale))
        duplicate = service.run("start")
        assert duplicate.returncode != 0 and "旧版 Xbum 已在运行" in duplicate.stderr
        tree = [psutil.Process(legacy.pid), *psutil.Process(legacy.pid).children()]
        result = service.run("stop")
        assert result.returncode == 0, result.stdout + result.stderr
        service.assert_gone(tree)
        assert legacy.wait(timeout=5) == 0
        # Even a matching creation time cannot authorize an unrelated command.
        stale["created"] = psutil.Process(unrelated.pid).create_time()
        (runtime / "run.lock").write_text(json.dumps(stale))
        assert "无需停止" in service.run("stop").stdout
        assert unrelated.poll() is None and neighbour.poll() is None
    finally:
        script.write_text(current)
        for process in [legacy, neighbour, unrelated]:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


def test_child_startup_failure_cleans_services_and_releases_lock(service):
    data = Path(service.env["DATA_DIR"])
    data.mkdir()
    with open(data / "worker.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        failed = service.launch()
        assert failed.wait(timeout=15) != 0
        assert not service.run("stop").returncode
        # No leftover API/Ollama listener despite Worker failure.
        for port in [service.port, service.ollama_port]:
            with socket.socket() as sock:
                assert sock.connect_ex(("127.0.0.1", port)) != 0
    recovered = service.launch()
    service.ready()
    assert recovered.poll() is None


def test_restart_aborts_if_old_service_does_not_stop(service, monkeypatch):
    script = service.root / "scripts/run.py"
    spec = importlib.util.spec_from_file_location("lifecycle", script)
    lifecycle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lifecycle)
    current = script.read_text()
    script.write_text(
        "import signal, time\nfrom pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "Path('ignoring-term').touch()\ntime.sleep(120)\n"
    )
    stubborn = subprocess.Popen([sys.executable, "scripts/run.py"], cwd=service.root)
    try:
        eventually(lambda: (service.root / "ignoring-term").exists())
        script.write_text(current)
        monkeypatch.setattr(lifecycle, "STOP_TIMEOUT", 0.2)
        monkeypatch.setattr(sys, "argv", ["scripts/run.py", "restart"])
        monkeypatch.chdir(service.root)
        with pytest.raises(SystemExit, match="停止超时.*未启动新实例"):
            lifecycle.main()
        assert stubborn.poll() is None
        assert not (service.root / "runtime/run.lock").exists()
    finally:
        stubborn.kill()
        stubborn.wait(timeout=5)


def test_shutdown_cleans_a_model_like_child_in_its_own_session(service):
    spec = importlib.util.spec_from_file_location("lifecycle_cleanup", service.root / "scripts/run.py")
    lifecycle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lifecycle)
    child_code = (
        "import signal, time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); Path('model-ready').touch(); time.sleep(120)"
    )
    parent_code = (
        "import subprocess, sys, time; from pathlib import Path; "
        f"p = subprocess.Popen([sys.executable, '-c', {child_code!r}], start_new_session=True); "
        "Path('model-pid').write_text(str(p.pid)); time.sleep(120)"
    )
    parent = subprocess.Popen([sys.executable, "-c", parent_code], cwd=service.root, start_new_session=True)
    child = None
    try:
        eventually(lambda: (service.root / "model-ready").exists())
        child = psutil.Process(int((service.root / "model-pid").read_text()))
        lifecycle.shutdown([parent])
        assert parent.returncode == -signal.SIGTERM
        service.assert_gone([child])
    finally:
        if child and alive(child):
            child.kill()
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
