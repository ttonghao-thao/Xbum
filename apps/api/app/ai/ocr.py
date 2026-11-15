"""Offline macOS Vision OCR, invoked only inside the isolated worker process."""

import json
import os
import platform
import subprocess
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db.collection_features import PhotoText
from app.db.models import Job, Photo
from app.workers.queue import enqueue

OCR_VERSION = "apple-vision-text-1"


def recognize(path):
    if platform.system() != "Darwin" or not Path("/usr/bin/swift").is_file():
        return {"ok": False, "code": "model_missing"}
    script = Path(__file__).resolve().parents[4] / "scripts" / "ocr.swift"
    cache = settings.data_dir / "ocr-cache"
    cache.mkdir(exist_ok=True, mode=0o700)
    result = subprocess.run(
        ["/usr/bin/swift", str(script), str(Path(path).resolve())],
        capture_output=True,
        timeout=settings.model_timeout,
        env={**os.environ, "CLANG_MODULE_CACHE_PATH": str(cache)},
    )
    if result.returncode:
        return {"ok": False, "code": "resource_error"}
    data = json.loads(result.stdout)
    # Bound recognizer output before storing any model-produced values.
    regions = []
    for region in data.get("regions", [])[:1000]:
        text = str(region.get("text", ""))[:4000]
        bounds = region.get("bbox", [])
        if len(bounds) == 4 and all(isinstance(x, (float, int)) and 0 <= x <= 1 for x in bounds):
            regions.append({"text": text, "bbox": bounds, "confidence": max(0, min(1, region.get("confidence", 0)))})
    return {
        "ok": True,
        "text": "\n".join(r["text"] for r in regions)[:64000],
        "regions": regions,
        "version": OCR_VERSION,
    }


def reconcile_ocr(db, household):
    """Only adds missing OCR work; never reruns the existing vision pipeline."""
    if household.settings_json.get("jobs_paused") or not household.settings_json.get("ocr_enabled", True):
        return 0
    texts = {r.photo_id: r for r in db.scalars(select(PhotoText).join(Photo).where(Photo.household_id == household.id))}
    count = 0
    for photo in db.scalars(
        select(Photo).where(
            Photo.household_id == household.id,
            Photo.visibility == "family",
            Photo.is_hidden.is_(False),
            Photo.is_deleted.is_(False),
        )
    ):
        row = texts.get(photo.id)
        if row and row.model_version == OCR_VERSION:
            continue
        key = f"photo:{photo.id}:ocr:{OCR_VERSION}"
        if db.scalar(select(Job.id).where(Job.idempotency_key == key)):
            continue
        enqueue(db, household.id, "photo", photo.id, "ocr", key=key, priority=-5)
        count += 1
    return count
