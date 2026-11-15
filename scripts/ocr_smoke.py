#!/usr/bin/env python3
"""Explicit real Apple Vision check using generated text, never household photos.

Run: .venv/bin/python scripts/ocr_smoke.py
Official contract: https://developer.apple.com/documentation/vision/vnrecognizetextrequest
"""

import json
import os
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main():
    with tempfile.TemporaryDirectory(prefix="xbum-ocr-smoke-") as temp:
        os.environ["DATA_DIR"] = temp
        from app.workers.worker import isolated_provider
        import psutil

        path = Path(temp) / "synthetic-text.png"
        image = Image.new("RGB", (1600, 600), "white")
        draw = ImageDraw.Draw(image)
        fonts = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Supplemental/Songti.ttc",
        ]
        font_path = next((f for f in fonts if Path(f).is_file()), None)
        if font_path is None:
            raise RuntimeError("No installed Chinese font for generated OCR smoke input")
        font = ImageFont.truetype(font_path, 88)
        draw.text((80, 80), "XBUM LOCAL 2026", fill="black", font=font)
        draw.text((80, 280), "家庭记忆 相册", fill="black", font=font)
        image.save(path)
        started = time.monotonic()
        result = isolated_provider("ocr", path=str(path.resolve()))
        report = {
            "input": "generated bilingual text",
            "seconds": round(time.monotonic() - started, 3),
            "ok": result.get("ok"),
            "version": result.get("version"),
            "region_count": len(result.get("regions", [])),
            "text": result.get("text", ""),
            "code": result.get("code"),
            "worker_children_after_task": len(psutil.Process().children(recursive=True)),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        assert result.get("ok") and "XBUM" in result["text"] and "2026" in result["text"]
        assert "家庭" in result["text"] and result["regions"]


if __name__ == "__main__":
    main()
