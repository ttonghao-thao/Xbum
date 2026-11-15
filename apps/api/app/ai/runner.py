import json
import sys
from pathlib import Path

from app.ai.providers import InvalidModelOutput, ModelMissing, OllamaVision, OpenCVFaces, SigLIP2, WhisperCpp
from pydantic import ValidationError
import httpx


def main():
    request = json.load(sys.stdin)
    kind = request["kind"]
    output = Path(request["output"])
    try:
        if kind in {"embedding", "text_embedding"}:
            provider = SigLIP2()
            try:
                vector = (
                    provider.embed_text(request["text"])
                    if kind == "text_embedding"
                    else provider.embed_image(Path(request["path"]))
                )
                result = {
                    "vector": vector.tolist(),
                    "version": provider.version,
                    "model": "google/siglip2-base-patch16-224",
                }
                result["device"] = provider.device
                if provider.device == "mps":
                    result["mps_driver_allocated_bytes"] = provider.torch.mps.driver_allocated_memory()
            finally:
                provider.close()
        elif kind == "vlm":
            result = OllamaVision().analyze_photo(Path(request["path"]))
        elif kind == "faces":
            result = {"faces": OpenCVFaces().detect_faces(Path(request["path"]))}
        elif kind == "asr":
            provider = WhisperCpp()
            result = {"transcript": provider.transcribe(Path(request["path"])), "version": provider.version}
        else:
            raise ValueError("unknown provider")
        output.write_text(json.dumps({"ok": True, **result}, ensure_ascii=False))
    except ModelMissing:
        output.write_text(json.dumps({"ok": False, "code": "model_missing"}))
    except (InvalidModelOutput, ValidationError, json.JSONDecodeError, KeyError):
        output.write_text(json.dumps({"ok": False, "code": "invalid_model_json"}))
    except (httpx.HTTPError, ConnectionError):
        output.write_text(json.dumps({"ok": False, "code": "model_unavailable"}))
    except ValueError:
        output.write_text(json.dumps({"ok": False, "code": "unsupported_media"}))
    except Exception:
        output.write_text(json.dumps({"ok": False, "code": "resource_error"}))


if __name__ == "__main__":
    main()
