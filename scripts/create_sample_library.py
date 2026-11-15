#!/usr/bin/env python3
"""Generate clearly synthetic, deterministic images; no personal photos or fake AI results."""

import argparse
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def generate(destination, count):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    records = []
    for index in range(count):
        rng = random.Random(index)
        image = Image.new("RGB", (960, 640), (220 + index % 30, 213 + index % 35, 190 + index % 40))
        draw = ImageDraw.Draw(image)
        for _ in range(35):
            x, y = rng.randrange(900), rng.randrange(580)
            draw.ellipse(
                (x, y, x + rng.randrange(10, 130), y + rng.randrange(10, 100)),
                fill=tuple(rng.randrange(80, 240) for _ in range(3)),
            )
        draw.rectangle((0, 510, 960, 640), fill=(62, 70, 59))
        draw.text(
            (30, 545),
            f"SYNTHETIC TEST IMAGE {index + 1:04d}",
            font=ImageFont.load_default(size=30),
            fill=(240, 230, 200),
        )
        # Several known dates/events, including a historical calendar memory.
        taken = datetime(2024 + (index // 50) % 2, 11, 14, 10, tzinfo=UTC) + timedelta(hours=(index % 50) * 0.1)
        exif = Image.Exif()
        if index % 10:
            exif[36867] = taken.strftime("%Y:%m:%d %H:%M:%S")
        path = destination / f"synthetic-{index + 1:04d}.jpg"
        image.save(path, "JPEG", quality=88, exif=exif)
        records.append(
            {"file": path.name, "known_taken_at": taken.isoformat() if index % 10 else None, "synthetic": True}
        )
    (destination / "manifest.json").write_text(json.dumps(records, indent=2))
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--output", default="data/samples")
    args = parser.parse_args()
    generate(args.output, args.count)
    print(f"Generated {args.count} synthetic images at {args.output}; upload explicitly in /upload")
