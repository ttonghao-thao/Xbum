#!/usr/bin/env python3
"""Real synthetic-media IO/quality/queue benchmark; never reports fake model throughput."""

import argparse
import json
import os
import platform
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import psutil


def run(count, output):
    with tempfile.TemporaryDirectory(prefix=f"xbum-benchmark-{count}-") as temp:
        os.environ["DATA_DIR"] = temp
        os.environ["DATABASE_URL"] = f"sqlite:///{temp}/album.sqlite3"
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, stdout=subprocess.DEVNULL)
        from app.core.config import defaults
        from app.core.security import Actor, password_hash
        from app.db.models import Household, Job, Membership, Photo, User
        from app.db.session import SessionLocal
        from app.services.photos import ingest
        from app.services.recommendations import build_daily_playlist
        from app.workers.queue import claim
        from app.workers.worker import run_job
        from create_sample_library import generate
        from sqlalchemy import func, select

        password = secrets.token_urlsafe(24)
        with SessionLocal() as db:
            config = {
                **defaults(),
                "embedding_enabled": False,
                "vlm_enabled": False,
                "quiet_start": "00:00",
                "quiet_end": "00:00",
            }
            h = Household(name="合成性能测试", timezone="Asia/Shanghai", settings_json=config)
            user = User(username="benchmark", display_name="合成测试", password_hash=password_hash(password))
            db.add_all([h, user])
            db.flush()
            db.add(Membership(household_id=h.id, user_id=user.id, role="owner"))
            db.commit()
            actor = Actor(user.id, h.id, "owner")
        process = psutil.Process()
        peak = [0]
        stopped = threading.Event()

        def measure():
            while not stopped.wait(0.05):
                peak[0] = max(peak[0], process.memory_info().rss)

        monitor = threading.Thread(target=measure, daemon=True)
        monitor.start()
        generated = time.monotonic()
        records = generate(Path(temp) / "samples", count)
        generation_s = time.monotonic() - generated
        started = time.monotonic()
        with SessionLocal() as db:
            for record in records:
                ingest(
                    db,
                    actor,
                    (Path(temp) / "samples" / record["file"]).read_bytes(),
                    True,
                    "Asia/Shanghai",
                    "image/jpeg",
                )
        import_s = time.monotonic() - started
        print(f"{count}: imported in {import_s:.2f}s", flush=True)
        started = time.monotonic()
        completed = 0
        with SessionLocal() as db:
            while True:
                job = claim(db)
                if not job:
                    waiting = db.scalar(select(func.count()).select_from(Job).where(Job.state == "queued"))
                    if not waiting:
                        break
                    time.sleep(0.25)
                    continue
                run_job(db, job)
                completed += 1
            pipeline_s = time.monotonic() - started
            started = time.monotonic()
            build_daily_playlist(db, db.get(Household, actor.household_id))
            db.commit()
            recommend_ms = (time.monotonic() - started) * 1000
            imported = db.scalar(select(func.count()).select_from(Photo))
            failed = db.scalar(select(func.count()).select_from(Job).where(Job.state == "failed"))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                try:
                    if (
                        httpx.get(
                            f"http://127.0.0.1:{port}/api/v1/system/health", timeout=1, trust_env=False
                        ).status_code
                        == 200
                    ):
                        break
                except httpx.HTTPError:
                    time.sleep(0.1)
            result = subprocess.run(
                ["node", "scripts/measure_display.mjs"],
                input=json.dumps(
                    {"baseURL": f"http://127.0.0.1:{port}", "username": "benchmark", "password": password}
                ),
                text=True,
                capture_output=True,
                timeout=60,
                check=True,
            )
            browser = json.loads(result.stdout)
        finally:
            server.terminate()
            server.wait(timeout=10)
            stopped.set()
            monitor.join()
        result = {
            "count": count,
            "imported": imported,
            "synthetic_generation_s": round(generation_s, 3),
            "import_s": round(import_s, 3),
            "import_photos_per_s": round(count / import_s, 2),
            "metadata_quality_queue_s": round(pipeline_s, 3),
            "non_ai_photos_per_s": round(count / pipeline_s, 2),
            "completed_jobs": completed,
            "failed_jobs": failed,
            "recommendation_ms": round(recommend_ms, 2),
            "peak_benchmark_rss_mb": round(peak[0] / 1024**2, 1),
            "model_inference": "disabled; no fake provider; model throughput not measured",
            **browser,
            "machine": platform.machine(),
            "physical_memory_gb": psutil.virtual_memory().total / 1024**3,
        }
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args.count, args.output)
