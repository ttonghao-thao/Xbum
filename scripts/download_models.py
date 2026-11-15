#!/usr/bin/env python3
"""Network access is restricted to this explicit installation program, never inference."""

import argparse
import hashlib
import json
import os
import subprocess
import re
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from app.core.config import settings

SIGLIP_REVISION = "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2"
ZOO_REVISION = "48b10fa505aeb51fbd127fc60a5c660040e823b7"
WHISPER_REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"
VLM_MANIFEST_SHA256 = "afc393e3572c07db0bc84aa460d928a16c75f3fbb9ca9e0dccc4fbd2dafb573e"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download(url, path, expected=None):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() and expected and sha256(path) == expected:
        return
    temporary = path.with_suffix(path.suffix + ".part")
    print(f"Downloading {path.name}", flush=True)
    if expected:
        # Immutable, SHA-256-verified weights support bounded, resumable range downloads.
        with httpx.stream("GET", url, headers={"Range": "bytes=0-0"}, follow_redirects=True, timeout=120) as probe:
            match = re.fullmatch(r"bytes 0-0/(\d+)", probe.headers.get("content-range", ""))
            total = int(match.group(1)) if probe.status_code == 206 and match else 0
        if total > 16 * 1024 * 1024:
            offset = temporary.stat().st_size if temporary.exists() else 0
            if offset > total:
                temporary.unlink()
                offset = 0
            chunks = [
                (begin, min(total - 1, begin + 32 * 1024 * 1024 - 1))
                for begin in range(offset, total, 32 * 1024 * 1024)
            ]

            def fetch_chunk(bounds):
                begin, end = bounds
                target = temporary.with_name(temporary.name + f".chunk-{begin}")
                if target.exists() and target.stat().st_size == end - begin + 1:
                    return target
                for attempt in range(4):
                    try:
                        with httpx.stream(
                            "GET", url, headers={"Range": f"bytes={begin}-{end}"}, follow_redirects=True, timeout=120
                        ) as response:
                            response.raise_for_status()
                            if response.headers.get("content-range") != f"bytes {begin}-{end}/{total}":
                                raise RuntimeError("Model server returned an unexpected byte range")
                            with target.open("wb") as stream:
                                for block in response.iter_bytes(1024 * 1024):
                                    stream.write(block)
                        if target.stat().st_size != end - begin + 1:
                            raise RuntimeError("Incomplete model range")
                        return target
                    except (httpx.HTTPError, RuntimeError):
                        if attempt == 3:
                            raise
                        time.sleep(2**attempt)

            with ThreadPoolExecutor(max_workers=6) as pool:
                parts = list(pool.map(fetch_chunk, chunks))
            with temporary.open("ab") as stream:
                for part in parts:
                    with part.open("rb") as source:
                        while block := source.read(1024 * 1024):
                            stream.write(block)
                    part.unlink()
            if sha256(temporary) != expected:
                temporary.unlink()
                raise RuntimeError(f"SHA-256 mismatch for {path.name}")
            os.replace(temporary, path)
            return
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        with temporary.open("wb") as stream:
            for chunk in response.iter_bytes(1024 * 1024):
                stream.write(chunk)
    actual = sha256(temporary)
    if expected and actual != expected:
        temporary.unlink()
        raise RuntimeError(f"SHA-256 mismatch for {path.name}")
    os.replace(temporary, path)


def hf_metadata(repo, revision):
    response = httpx.get(
        f"https://huggingface.co/api/models/{repo}/revision/{revision}",
        params={"blobs": "true"},
        timeout=60,
        follow_redirects=True,
    )
    response.raise_for_status()
    data = response.json()
    if data["sha"] != revision:
        raise RuntimeError("Repository revision mismatch")
    return data


def siglip():
    root = settings.model_dir / "siglip2"
    metadata = hf_metadata(settings.siglip_model, SIGLIP_REVISION)
    for item in metadata["siblings"]:
        name = item["rfilename"]
        if "/" in name or not name.endswith((".json", ".model", ".safetensors")):
            continue
        expected = item.get("lfs", {}).get("sha256")
        download(
            f"https://huggingface.co/{settings.siglip_model}/resolve/{SIGLIP_REVISION}/{name}", root / name, expected
        )
    (root / "xbum-version.json").write_text(json.dumps({"revision": SIGLIP_REVISION, "repo": settings.siglip_model}))


def faces():
    for folder, name in [
        ("face_detection_yunet", "face_detection_yunet_2023mar.onnx"),
        ("face_recognition_sface", "face_recognition_sface_2021dec.onnx"),
    ]:
        suffix = f"models/{folder}/{name}"
        response = httpx.get(
            f"https://raw.githubusercontent.com/opencv/opencv_zoo/{ZOO_REVISION}/{suffix}",
            timeout=60,
            follow_redirects=True,
        )
        response.raise_for_status()
        pointer = response.text
        expected = next(line.split("sha256:")[1] for line in pointer.splitlines() if line.startswith("oid sha256:"))
        download(
            f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{ZOO_REVISION}/{suffix}",
            settings.model_dir / name,
            expected,
        )


def whisper():
    data = hf_metadata("ggerganov/whisper.cpp", WHISPER_REVISION)
    item = next(i for i in data["siblings"] if i["rfilename"] == "ggml-small.bin")
    download(
        f"https://huggingface.co/ggerganov/whisper.cpp/resolve/{WHISPER_REVISION}/ggml-small.bin",
        settings.whisper_model,
        item["lfs"]["sha256"],
    )


def vlm():
    if "cloud" in settings.vlm_model.lower():
        raise RuntimeError("Only local models are supported")
    response = httpx.get(settings.ollama_base_url + "/api/tags", timeout=10, trust_env=False)
    response.raise_for_status()
    model = next((m for m in response.json()["models"] if m["name"] == settings.vlm_model), None)
    expected = VLM_MANIFEST_SHA256 if settings.vlm_model == "qwen3-vl:2b-instruct" else None
    if model is None:
        subprocess.run(["ollama", "pull", settings.vlm_model], check=True)
        response = httpx.get(settings.ollama_base_url + "/api/tags", timeout=10, trust_env=False)
        response.raise_for_status()
        model = next(m for m in response.json()["models"] if m["name"] == settings.vlm_model)
    if expected and model["digest"].removeprefix("sha256:") != expected:
        raise RuntimeError("Installed Qwen digest differs from pinned manifest; explicit version review required")
    (settings.model_dir / "ollama-model.json").write_text(json.dumps(model, indent=2))


def vlm_cache():
    """Explicit install alternative for networks incompatible with Ollama's downloader.

    Use official immutable registry blobs + local documented blob API; do not
    disable Ollama's redirect validation or change its on-disk storage layout.
    """
    registry = "https://registry.ollama.ai/v2/library/qwen3-vl"
    response = httpx.get(registry + "/manifests/2b-instruct", timeout=30, follow_redirects=True)
    response.raise_for_status()
    if hashlib.sha256(response.content).hexdigest() != VLM_MANIFEST_SHA256:
        raise RuntimeError("Qwen registry manifest changed; review MODEL_MANIFEST before updating")
    (settings.model_dir / "ollama-manifest.json").write_bytes(response.content)
    manifest = response.json()
    with httpx.Client(timeout=600, trust_env=False) as client:
        for blob in [manifest["config"], *manifest["layers"]]:
            digest = blob["digest"]
            if client.head(settings.ollama_base_url + "/api/blobs/" + digest).status_code == 200:
                continue
            path = settings.model_dir / "ollama-install-cache" / digest.replace(":", "-")
            download(registry + "/blobs/" + digest, path, digest.split(":")[1])
            with path.open("rb") as stream:
                result = client.post(
                    settings.ollama_base_url + "/api/blobs/" + digest,
                    content=stream,
                    headers={"Content-Type": "application/octet-stream", "Content-Length": str(path.stat().st_size)},
                )
                result.raise_for_status()
    vlm()
    # Ollama now owns verified blobs; installer cache is redundant.
    import shutil

    shutil.rmtree(settings.model_dir / "ollama-install-cache", ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "component",
        choices=["all", "siglip", "faces", "whisper", "vlm", "vlm-cache", "verify"],
        default="all",
        nargs="?",
    )
    args = parser.parse_args()
    settings.model_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = settings.model_dir / "installed-lock.json"
    if args.component == "verify":
        data = json.loads(lock_path.read_text())
        for relative, info in data["files"].items():
            path = settings.model_dir / relative
            if not path.is_file() or sha256(path) != info["sha256"]:
                raise SystemExit(f"Integrity failed: {relative}")
        print("All installed model files verified")
        return
    for name, install in [
        ("siglip", siglip),
        ("faces", faces),
        ("whisper", whisper),
        ("vlm", vlm),
        ("vlm-cache", vlm_cache),
    ]:
        if args.component == name or (args.component == "all" and name != "vlm-cache"):
            install()
    files = {
        str(p.relative_to(settings.model_dir)): {"sha256": sha256(p), "bytes": p.stat().st_size}
        for p in settings.model_dir.rglob("*")
        if p.is_file() and p.name != "installed-lock.json" and ".part" not in p.name
    }
    lock_path.write_text(
        json.dumps(
            {
                "siglip_revision": SIGLIP_REVISION,
                "opencv_zoo_revision": ZOO_REVISION,
                "whisper_revision": WHISPER_REVISION,
                "files": files,
            },
            indent=2,
        )
    )
    print(f"Installed and hashed {len(files)} files; total {sum(f['bytes'] for f in files.values())} bytes")


if __name__ == "__main__":
    main()
