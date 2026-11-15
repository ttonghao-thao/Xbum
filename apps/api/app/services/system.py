import shutil
import platform

import psutil

import httpx

from app.core.config import settings


def processing_status(household):
    """Explain resource limits before claiming new background work; never kill it."""
    from app.services.calendar import local_now

    config = household.settings_json
    hour = local_now(household.timezone).hour
    start, end = config.get("background_start_hour", 0), config.get("background_end_hour", 0)
    in_window = start == end or (start <= hour < end if start < end else hour >= start or hour < end)
    memory = psutil.virtual_memory().available // (1024 * 1024)
    disk = shutil.disk_usage(settings.data_dir).free // (1024 * 1024)
    reasons = []
    if config.get("jobs_paused"):
        reasons.append("已手动暂停后台任务")
    if not in_window:
        reasons.append("尚未进入允许的后台分析时段")
    if memory < config.get("min_free_memory_mb", 512):
        reasons.append("可用内存低于设置的保留量")
    if disk < config.get("min_free_disk_mb", 1024):
        reasons.append("可用磁盘低于设置的保留量")
    return {"can_process_background": not reasons, "reasons": reasons, "available_memory_mb": memory,
            "available_disk_mb": disk, "in_window": in_window}


def model_status():
    path = settings.model_dir / "siglip2"
    embedding = (
        (path / "config.json").is_file() and (path / "xbum-version.json").is_file() and any(path.glob("*.safetensors"))
    )
    ollama, version, installed = False, "", False
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(settings.ollama_base_url + "/api/tags")
            response.raise_for_status()
            ollama = True
            matches = [m for m in response.json().get("models", []) if m["name"] == settings.vlm_model]
            installed = bool(matches)
            version = matches[0].get("digest", "") if matches else ""
    except (httpx.HTTPError, ValueError):
        pass
    face = all(
        (settings.model_dir / name).is_file()
        for name in ["face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx"]
    )
    ffmpeg = shutil.which("ffmpeg")
    speech = bool(shutil.which(settings.whisper_bin)) and settings.whisper_model.is_file()
    space = shutil.disk_usage(settings.data_dir)
    return {
        "ollama": {"running": ollama, "installed": installed, "model": settings.vlm_model, "version": version},
        "siglip2": {"installed": embedding, "model": settings.siglip_model},
        "faces": {"installed": face, "models": "YuNet + SFace"},
        "whisper": {"installed": speech, "message": "本地语音可用" if speech else "尚未安装本地语音模型"},
        "ffmpeg": {"installed": bool(ffmpeg)},
        "ocr": {"installed": platform.system() == "Darwin" and bool(shutil.which("swift")),
                "model": "Apple Vision · 本地文字识别"},
        "data_dir": str(settings.data_dir.resolve()),
        "disk_free_bytes": space.free,
        "disk_total_bytes": space.total,
        "ai_concurrency": 1,
        "runtime_network": "loopback inference / local_files_only",
    }
