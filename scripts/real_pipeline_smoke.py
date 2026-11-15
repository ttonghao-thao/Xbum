#!/usr/bin/env python3
"""HTTP API -> durable queue -> actual Worker -> actual local models, isolated data."""

import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from app.ai.providers import ffmpeg_binary
from create_sample_library import generate


def main():
    root = Path("runtime/real-pipeline")
    root.mkdir(parents=True, exist_ok=True)
    generate(root, 2)
    with tempfile.TemporaryDirectory(prefix="xbum-real-pipeline-") as temp:
        env = {
            **os.environ,
            "DATA_DIR": temp,
            "DATABASE_URL": f"sqlite:///{temp}/album.sqlite3",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        log = (root / "services.log").open("w")
        api = subprocess.Popen(
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
            env=env,
            stdout=log,
            stderr=log,
        )
        worker = subprocess.Popen([sys.executable, "-m", "app.workers.worker"], env=env, stdout=log, stderr=log)
        started = time.monotonic()
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=30) as client:
                for _ in range(100):
                    try:
                        if client.get("/api/v1/system/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        time.sleep(0.1)
                response = client.post(
                    "/api/v1/auth/setup",
                    json={
                        "username": "real-smoke",
                        "password": secrets.token_urlsafe(24),
                        "display_name": "合成验证",
                        "household_name": "本地模型验证",
                    },
                )
                response.raise_for_status()
                client.headers["X-CSRF-Token"] = response.json()["csrf"]
                response = client.patch(
                    "/api/v1/settings", json={"speech_enabled": True, "quiet_start": "00:00", "quiet_end": "00:00"}
                )
                response.raise_for_status()
                response = client.post(
                    "/api/v1/photos/upload",
                    data={"shared": "true"},
                    files={"file": ("synthetic.jpg", (root / "synthetic-0002.jpg").read_bytes(), "image/jpeg")},
                )
                response.raise_for_status()
                pid = response.json()["photo_id"]
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    photo = client.get("/api/v1/photos/" + pid).json()
                    jobs = photo["jobs"]
                    if all(j["state"] in {"succeeded", "skipped", "failed"} for j in jobs):
                        break
                    time.sleep(0.5)
                assert all(j["state"] in {"succeeded", "skipped"} for j in jobs), [
                    (j["job_type"], j["state"], j["error_code"]) for j in jobs
                ]
                assert photo["analysis"]["quality_score"] is not None and photo["analysis"]["status"] == "succeeded"
                with sqlite3.connect(Path(temp) / "album.sqlite3") as db:
                    embedding = db.execute(
                        "SELECT dimension,model_version,length(data_blob) FROM embeddings WHERE photo_id=?", (pid,)
                    ).fetchone()
                    assert embedding and embedding[0] == 768 and embedding[2] == 768 * 4
                search = client.post("/api/v1/search", json={"query": "几何形状和多彩的圆形"}).json()
                jid = search["job_id"]
                assert jid
                while time.monotonic() < deadline:
                    state = client.get("/api/v1/jobs/" + jid).json()["state"]
                    if state in {"succeeded", "failed"}:
                        break
                    time.sleep(0.5)
                assert state == "succeeded", state
                semantic = client.post(
                    "/api/v1/search", json={"query": "几何形状和多彩的圆形", "query_id": search["query_id"]}
                ).json()
                audio = root / "query.aiff"
                wav = root / "query.wav"
                subprocess.run(
                    ["say", "-v", "Tingting", "-o", str(audio), "去年春节的全家合影。我们一起去海边旅行。"], check=True
                )
                subprocess.run(
                    [
                        ffmpeg_binary(),
                        "-nostdin",
                        "-v",
                        "error",
                        "-y",
                        "-i",
                        str(audio),
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        str(wav),
                    ],
                    check=True,
                )
                response = client.post(
                    "/api/v1/memories/audio",
                    data={"photo_id": pid},
                    files={"file": ("query.wav", wav.read_bytes(), "audio/wav")},
                )
                response.raise_for_status()
                mid = response.json()["memory_id"]
                while time.monotonic() < deadline:
                    memory = client.get("/api/v1/memories/audio/" + mid).json()
                    if any(j["state"] in {"succeeded", "failed"} for j in memory["jobs"]):
                        break
                    time.sleep(0.5)
                assert memory["transcript"] and memory["jobs"][0]["state"] == "succeeded", memory["jobs"]
                voice = client.post(
                    "/api/v1/speech/transcribe", files={"file": ("query.wav", wav.read_bytes(), "audio/wav")}
                )
                voice.raise_for_status()
                query_memory_id = voice.json()["memory_id"]
                while time.monotonic() < deadline:
                    query_memory = client.get("/api/v1/memories/audio/" + query_memory_id).json()
                    if any(j["state"] in {"succeeded", "failed"} for j in query_memory["jobs"]):
                        break
                    time.sleep(0.5)
                assert query_memory["transcript"]
                voice_search = client.post(
                    "/api/v1/search", json={"query": query_memory["transcript"], "semantic": False}
                ).json()
                assert voice_search["time_basis"] == "春节" and voice_search["items"] == []
                client.patch(
                    "/api/v1/memories/audio/" + mid, json={"transcript": "人工核对后的合成口述记忆"}
                ).raise_for_status()
                client.post("/api/v1/playlists/regenerate").raise_for_status()
                code = client.post("/api/v1/display/devices", json={"name": "real-smoke-display"}).json()["code"]
                client.post("/api/v1/display/pair", json={"code": code}).raise_for_status()
                feed = client.get("/api/v1/display/feed").json()
                assert len(feed["items"]) == 1
                client.patch("/api/v1/photos/" + pid, json={"visibility": "private"}).raise_for_status()
                assert client.get("/api/v1/display/feed").json()["items"] == []
                result = {
                    "ok": True,
                    "wall_s": round(time.monotonic() - started, 3),
                    "fake_provider": False,
                    "network": "local model loading + loopback Ollama; no physically disconnected OS test",
                    "photo_steps": [
                        {
                            "type": j["job_type"],
                            "state": j["state"],
                            "duration_ms": j["duration_ms"],
                            "model_version": j["model_version"],
                        }
                        for j in jobs
                    ],
                    "embedding_dimension": embedding[0],
                    "embedding_bytes": embedding[2],
                    "semantic_job_state": state,
                    "semantic_candidates": semantic["total"],
                    "asr_transcript_synthetic": memory["transcript"],
                    "asr_model_version": memory["jobs"][0]["model_version"],
                    "audio_correction": True,
                    "speech_query_transcript": query_memory["transcript"],
                    "speech_search_time_basis": voice_search["time_basis"],
                    "speech_search_candidate_count": voice_search["total"],
                    "display_after_withdrawal_empty": True,
                }
                (Path("runtime") / "real-pipeline.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
                print(json.dumps(result, ensure_ascii=False))
        finally:
            worker.terminate()
            api.terminate()
            worker.wait(timeout=10)
            api.wait(timeout=10)
            log.close()


if __name__ == "__main__":
    main()
