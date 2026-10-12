"""Assemble email-safe GIFs from the original, generated mascot sprites.

Development tool only: Pillow is not an application runtime dependency.
The source artwork was generated with the built-in image generation tool.
No remote images, fonts, scripts, or trackers are used by the resulting GIFs.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets" / "email"
SIZE = (400, 240)
BACKGROUND = (248, 251, 251, 255)
FRAME_COUNT = 24
FRAME_MS = 150


def read_sprites(mood: str) -> list[Image.Image]:
    """Crop generated poses; keep their shared scale and lower baseline."""
    image = Image.open(ASSETS / f"{mood}-source.png").convert("RGBA")
    if mood == "neutral":
        return [image.crop(image.getchannel("A").getbbox())]
    panels = [image.crop((round(image.width * i / 3), 0,
                           round(image.width * (i + 1) / 3), image.height))
              for i in range(3)]
    # A common crop preserves the relative pose size and airborne jump.
    boxes = [panel.getchannel("A").getbbox() for panel in panels]
    common = (min(box[0] for box in boxes), min(box[1] for box in boxes),
              max(box[2] for box in boxes), max(box[3] for box in boxes))
    return [panel.crop(common) for panel in panels]


def frames_for(mood: str) -> list[Image.Image]:
    sprites = [ImageOps.contain(sprite, (262, 214), Image.Resampling.LANCZOS)
               for sprite in read_sprites(mood)]
    frames = []
    for index in range(FRAME_COUNT):
        phase = index / FRAME_COUNT * math.tau
        if mood == "uncertain":
            pose = (0, 1, 0, 2)[index // 6]
            vertical = round(math.sin(phase) * 2)
        elif mood == "celebrate":
            pose = (0, 1, 2, 0)[index // 6]
            vertical = -round(max(0, math.sin(phase)) * 4)
        else:
            pose = 0
            vertical = round(math.sin(phase) * 2)
        sprite = sprites[pose]
        frame = Image.new("RGBA", SIZE, BACKGROUND)
        position = ((SIZE[0] - sprite.width) // 2,
                    SIZE[1] - sprite.height - 10 + vertical)
        frame.alpha_composite(sprite, position)
        frames.append(frame.convert("RGB"))
    return frames


def build(mood: str) -> dict:
    frames = frames_for(mood)
    # All frames share a palette to avoid color changes between poses.
    atlas = Image.new("RGB", (SIZE[0] * len(frames), SIZE[1]))
    for index, frame in enumerate(frames):
        atlas.paste(frame, (SIZE[0] * index, 0))
    palette = atlas.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    indexed = [frame.quantize(palette=palette, dither=Image.Dither.NONE)
               for frame in frames]
    target = ASSETS / f"{mood}.gif"
    indexed[0].save(target, save_all=True, append_images=indexed[1:],
                    duration=FRAME_MS, loop=1, disposal=2, optimize=True)
    frames[0].save(ASSETS / f"{mood}.png", optimize=True)
    with Image.open(target) as check:
        duration = sum(_duration(check, i) for i in range(check.n_frames))
        assert check.size == SIZE
        assert check.info.get("loop") == 1  # one repeat: two plays total
        assert duration == FRAME_COUNT * FRAME_MS
        assert target.stat().st_size < 500_000
        return {"file": target.name, "width": check.width, "height": check.height,
                "frames": check.n_frames, "duration_ms": duration,
                "total_plays": 2, "bytes": target.stat().st_size}


def _duration(image: Image.Image, frame: int) -> int:
    image.seek(frame)
    return image.info["duration"]


if __name__ == "__main__":
    result = [build(mood) for mood in ("uncertain", "celebrate", "neutral")]
    print(json.dumps(result, indent=2))
