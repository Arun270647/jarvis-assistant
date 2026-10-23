"""Render the app logo: a glowing crescent inside the HUD ring, in the app's cyan-on-navy palette.

The crescent is inherited from the personal build this project started from; a mark designed for
Jarvis is still to come (planning: 06_LICENSING.md, assets).

Writes src/gui/assets/jarvis.png (512 px) and jarvis.ico (16-256 px; the small
sizes drop the fine ticks and thicken the ring so they stay legible).

    python scripts/make_logo.py
"""
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "gui" / "assets"

NAVY_IN = (11, 42, 66)        # disc centre
NAVY_OUT = (3, 17, 30)        # --color-accent-900
CYAN = (53, 205, 250)         # --color-accent-400
CYAN_DEEP = (11, 134, 180)    # --color-accent-600
MOON_HI = (228, 251, 255)     # --color-accent-100
MOON_LO = (116, 225, 255)     # --color-accent-300


def radial(size, inner, outer, radius):
    """A radial gradient disc (RGBA) of the given radius, centred."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    c = size / 2
    for y in range(size):
        for x in range(size):
            d = math.hypot(x - c, y - c) / radius
            if d <= 1:
                t = d ** 1.6
                px[x, y] = tuple(int(inner[i] + (outer[i] - inner[i]) * t) for i in range(3)) + (255,)
    return img


def linear(size, top, bottom, angle_deg=-35):
    """A diagonal linear gradient fill (RGB)."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    a = math.radians(angle_deg)
    dx, dy = math.cos(a), math.sin(a)
    span = size * (abs(dx) + abs(dy))
    for y in range(size):
        for x in range(size):
            t = ((x - size / 2) * dx + (y - size / 2) * dy) / span + 0.5
            t = min(1, max(0, t))
            px[x, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return img


def circle(draw, cx, cy, r, **kw):
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), **kw)


def render(size: int, detailed: bool) -> Image.Image:
    S = size * 4                      # supersample, then downscale
    c = S / 2
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # Disc
    disc_r = S * 0.485
    img.alpha_composite(radial(S, NAVY_IN, NAVY_OUT, disc_r))

    # HUD ring: four arcs with gaps on the diagonals, plus an inner hairline.
    ring = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(ring)
    r = S * (0.415 if detailed else 0.40)
    w = int(S * (0.028 if detailed else 0.055))
    box = (c - r, c - r, c + r, c + r)
    gap = 14 if detailed else 0
    if gap:
        for start in (-45, 45, 135, 225):
            d.arc(box, start + gap / 2, start + 90 - gap / 2, fill=CYAN + (255,), width=w)
    else:
        d.ellipse(box, outline=CYAN + (255,), width=w)
    if detailed:
        r2 = S * 0.365
        d.ellipse((c - r2, c - r2, c + r2, c + r2), outline=CYAN_DEEP + (200,), width=max(2, int(S * 0.006)))
        # Ticks in the four gaps
        for ang in (-45, 45, 135, 225):
            a = math.radians(ang)
            r_in, r_out = S * 0.395, S * 0.445
            d.line((c + r_in * math.cos(a), c + r_in * math.sin(a), c + r_out * math.cos(a), c + r_out * math.sin(a)),
                   fill=CYAN + (255,), width=max(2, int(S * 0.012)))
    glow = ring.filter(ImageFilter.GaussianBlur(S * 0.018))
    img.alpha_composite(glow)
    img.alpha_composite(ring)

    # Crescent: big circle minus an offset circle. Opening faces upper right, so it also reads as a 'C'.
    mr = S * (0.25 if detailed else 0.27)
    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    mcx, mcy = c - S * 0.02, c + S * 0.005
    circle(md, mcx, mcy, mr, fill=255)
    cut = Image.new("L", (S, S), 0)
    circle(ImageDraw.Draw(cut), mcx + mr * 0.48, mcy - mr * 0.30, mr * 0.86, fill=255)
    mask = ImageChops.subtract(mask, cut)

    moon_glow = Image.new("RGBA", (S, S), CYAN + (0,))
    moon_glow.putalpha(mask.filter(ImageFilter.GaussianBlur(S * 0.05)).point(lambda v: int(v * 0.9)))
    img.alpha_composite(moon_glow)
    moon = linear(S, MOON_HI, MOON_LO).convert("RGBA")
    moon.putalpha(mask)
    img.alpha_composite(moon)

    if detailed:
        # A four-point star in the crescent's opening
        star = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        sd = ImageDraw.Draw(star)
        sx, sy, sr = c + S * 0.12, c - S * 0.10, S * 0.055
        k = sr * 0.22
        sd.polygon([(sx, sy - sr), (sx + k, sy - k), (sx + sr, sy), (sx + k, sy + k),
                    (sx, sy + sr), (sx - k, sy + k), (sx - sr, sy), (sx - k, sy - k)], fill=MOON_HI + (255,))
        img.alpha_composite(star.filter(ImageFilter.GaussianBlur(S * 0.012)))
        img.alpha_composite(star)

    return img.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    big = render(512, detailed=True)
    big.save(OUT / "jarvis.png")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [render(s, detailed=s >= 48) for s in sizes]
    frames[-1].save(OUT / "jarvis.ico", format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    print(f"Wrote {OUT / 'jarvis.png'} and {OUT / 'jarvis.ico'}")


if __name__ == "__main__":
    main()
