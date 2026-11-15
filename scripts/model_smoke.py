#!/usr/bin/env python3
"""Real local inference only; missing models are failures, never replaced by fakes."""

import argparse
import json
import os
import platform
import subprocess
import threading
import time
from pathlib import Path

import httpx
import psutil

from app.core.config import settings
from app.workers.worker import isolated_provider
from create_sample_library import generate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["all", "embedding", "text_embedding", "vlm", "faces", "asr"], default="all")
    args = parser.parse_args()
    root = Path("runtime/model-smoke")
    root.mkdir(parents=True, exist_ok=True)
    generate(root, 2)
    audio = root / "query.aiff"
    tts_ok = False
    if platform.system() == "Darwin":
        result = subprocess.run(
            ["say", "-v", "Tingting", "-o", str(audio), "去年春节的全家合影。我们一起去海边旅行。"], capture_output=True
        )
        tts_ok = result.returncode == 0
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    reports = []
    for kind in ["embedding", "text_embedding", "vlm", "faces", "asr"]:
        if args.only not in {"all", kind}:
            continue
        payload = (
            {"text": "几何形状和多彩的圆形"}
            if kind == "text_embedding"
            else {"path": str((audio if kind == "asr" else root / "synthetic-0002.jpg").resolve())}
        )
        if kind == "asr" and not tts_ok:
            reports.append({"kind": kind, "ok": False, "code": "local_chinese_tts_unavailable"})
            continue
        peak = [0]
        ollama_rss = [0]
        ollama_gpu = [0]
        stopped = threading.Event()
        process = psutil.Process()

        def monitor(
            stopped=stopped, process=process, peak=peak, kind=kind, ollama_rss=ollama_rss, ollama_gpu=ollama_gpu
        ):
            while not stopped.wait(0.05):
                try:
                    rss = sum(p.memory_info().rss for p in [process, *process.children(recursive=True)])
                    peak[0] = max(peak[0], rss)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                if kind == "vlm":
                    total = 0
                    for candidate in psutil.process_iter(["name", "memory_info"]):
                        try:
                            if "ollama" in (candidate.info["name"] or "").lower():
                                total += candidate.info["memory_info"].rss
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    ollama_rss[0] = max(ollama_rss[0], total)
                    try:
                        response = httpx.get(settings.ollama_base_url + "/api/ps", timeout=0.5, trust_env=False)
                        ollama_gpu[0] = max(
                            ollama_gpu[0], sum(m.get("size_vram", 0) for m in response.json().get("models", []))
                        )
                    except (httpx.HTTPError, ValueError):
                        pass

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        started = time.monotonic()
        result = isolated_provider(kind, **payload)
        elapsed = time.monotonic() - started
        stopped.set()
        thread.join()
        report = {
            "kind": kind,
            "ok": result.get("ok", False),
            "duration_s": round(elapsed, 3),
            "parent_and_child_peak_rss_mb": round(peak[0] / 1024**2, 1),
            "code": result.get("code"),
            "ollama_peak_rss_mb": round(ollama_rss[0] / 1024**2, 1),
            "ollama_reported_peak_model_gpu_mb": round(ollama_gpu[0] / 1024**2, 1),
            "worker_children_after_task": len(process.children(recursive=True)),
            "device": result.get("device"),
            "mps_driver_allocated_mb": round(result.get("mps_driver_allocated_bytes", 0) / 1024**2, 1),
        }
        if "vector" in result:
            import numpy as np

            vector = np.asarray(result["vector"])
            report["dimension"] = len(vector)
            report["l2_norm"] = float(np.linalg.norm(vector))
        if kind == "vlm" and result.get("ok"):
            report["analysis"] = result["analysis"]
            report["version"] = result["version"]
        if kind == "faces" and result.get("ok"):
            report["detected_faces"] = len(result["faces"])
            report["positive_face_accuracy"] = "not evaluated; synthetic geometric input contains no real face"
            from app.ai.providers import OpenCVFaces
            import cv2
            import numpy as np

            provider = OpenCVFaces()
            image = cv2.imread(payload["path"])
            # This is an explicitly synthetic landmark input to verify the SFace
            # adapter executes; it is not evidence of detection or identity accuracy.
            landmark = np.asarray(
                [[20, 20, 120, 120, 50, 60, 105, 60, 80, 85, 55, 115, 105, 115, 0.99]], dtype=np.float32
            )
            probe = provider.embed_faces(image, landmark)
            report["sface_synthetic_patch_dimension"] = len(probe[0]["vector"])
        if kind == "asr" and result.get("ok"):
            report["synthetic_tts_input"] = "去年春节的全家合影。我们一起去海边旅行。"
            report["transcript"] = result["transcript"]
            report["audio_source"] = "macOS Tingting local TTS; no personal recording"
        try:
            response = httpx.get(settings.ollama_base_url + "/api/ps", timeout=2, trust_env=False)
            report["ollama_loaded_after_task"] = len(response.json().get("models", []))
        except (httpx.HTTPError, ValueError):
            pass
        reports.append(report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    output = {
        "machine": platform.machine(),
        "physical_memory_gb": psutil.virtual_memory().total / 1024**3,
        "hf_runtime_offline": True,
        "network_boundary": "HF offline flags + local-only Ollama; OS network not physically disconnected",
        "results": reports,
    }
    path = Path("runtime") / ("model-smoke.json" if args.only == "all" else f"model-smoke-{args.only}.json")
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    if any(not r["ok"] for r in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
