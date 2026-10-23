"""Phone app icons from the Jarvis logo (scripts/make_logo.py) into mobile/assets.

    python scripts/make_app_icons.py
"""
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_logo import NAVY_OUT, render  # noqa: E402

ASSETS = Path(__file__).resolve().parents[1] / "mobile" / "assets"


def on_canvas(logo: Image.Image, size: int, fill=None) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), fill or (0, 0, 0, 0))
    canvas.alpha_composite(logo, ((size - logo.width) // 2, (size - logo.height) // 2))
    return canvas


def main():
    render(1024, detailed=True).save(ASSETS / "icon.png")
    # Adaptive icon: the launcher masks the outer third, so the logo sits in the 66% safe zone.
    on_canvas(render(640, detailed=True), 1024).save(ASSETS / "android-icon-foreground.png")
    Image.new("RGBA", (1024, 1024), NAVY_OUT + (255,)).save(ASSETS / "android-icon-background.png")
    mono = render(640, detailed=False)
    alpha = mono.convert("L").point(lambda v: 255 if v > 60 else 0)
    white = Image.new("RGBA", mono.size, (255, 255, 255, 0))
    white.putalpha(Image.composite(alpha, Image.new("L", mono.size, 0), mono.getchannel("A")))
    on_canvas(white, 1024).save(ASSETS / "android-icon-monochrome.png")
    on_canvas(render(512, detailed=True), 1024).save(ASSETS / "splash-icon.png")
    render(48, detailed=True).save(ASSETS / "favicon.png")
    print(f"Wrote icons to {ASSETS}")


if __name__ == "__main__":
    main()
