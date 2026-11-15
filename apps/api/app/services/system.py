import shutil

import httpx

from app.core.config import settings


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
        "data_dir": str(settings.data_dir.resolve()),
        "disk_free_bytes": space.free,
        "disk_total_bytes": space.total,
        "ai_concurrency": 1,
        "runtime_network": "loopback inference / local_files_only",
    }
