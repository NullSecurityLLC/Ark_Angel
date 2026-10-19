"""Draw the ArkAngel icon and write every format the project ships.

The mark: a solid amber map pin under a thin halo, on a near-black tile in
the report's palette. "Ark Angel": something watching over a location. It's
drawn from geometry, not a bitmap, so any size is crisp. Strokes get
proportionally thicker at 16-24 px so the halo survives in a taskbar.

Writes to packaging/icons/:
    ark-angel.svg        vector master (README, web)
    ark-angel.ico        Windows executable (16-256 px)
    ark-angel.icns       macOS executable (16-1024 px)
    ark-angel-512.png    README / release page
and src/ark_angel/report/favicon.png (32 px), embedded in HTML reports.

Usage: python scripts/build_icons.py
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging" / "icons"

TILE = (20, 18, 14, 255)       # --bg #14120e
AMBER = (224, 178, 92, 255)    # --accent #e0b25c
CORNER = 0.22                  # tile corner radius, fraction of size

# Geometry on a unit square (0..1, y down).
PIN_CX, PIN_CY, PIN_R, PIN_TIP = 0.5, 0.53, 0.19, 0.86
DOT_R = 0.072
HALO_CY, HALO_W, HALO_H = 0.235, 0.46, 0.12


def _halo_stroke(size: int) -> float:
    """Halo thickness as a fraction of the size; thicker when tiny so it stays visible."""
    return 0.034 if size >= 64 else 0.06 if size >= 32 else 0.085


def _pin_outline(cx: float, cy: float, r: float, tip_y: float, steps: int = 96) -> list[tuple[float, float]]:
    """A teardrop: the circle's top arc between the two tangents from the tip, then the tip."""
    alpha = math.acos(r / (tip_y - cy))  # angle between "straight down" and each tangent point
    start, end = math.pi / 2 + alpha, math.pi / 2 - alpha + 2 * math.pi
    points = [(cx + r * math.cos(start + (end - start) * i / steps),
               cy + r * math.sin(start + (end - start) * i / steps)) for i in range(steps + 1)]
    return points + [(cx, tip_y)]


def draw(size: int, supersample: int = 8) -> Image.Image:
    big = size * supersample
    s = float(big)
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, big - 1, big - 1], radius=CORNER * s, fill=TILE)
    d.polygon([(x * s, y * s) for x, y in _pin_outline(PIN_CX, PIN_CY, PIN_R, PIN_TIP)], fill=AMBER)
    d.ellipse([(PIN_CX - DOT_R) * s, (PIN_CY - DOT_R) * s, (PIN_CX + DOT_R) * s, (PIN_CY + DOT_R) * s], fill=TILE)
    stroke = _halo_stroke(size)
    d.ellipse([(0.5 - HALO_W / 2) * s, (HALO_CY - HALO_H / 2) * s,
               (0.5 + HALO_W / 2) * s, (HALO_CY + HALO_H / 2) * s],
              outline=AMBER, width=max(1, round(stroke * s)))
    return img.resize((size, size), Image.LANCZOS)


def svg() -> str:
    path = " ".join(("M" if i == 0 else "L") + f"{x * 1024:.1f},{y * 1024:.1f}"
                    for i, (x, y) in enumerate(_pin_outline(PIN_CX, PIN_CY, PIN_R, PIN_TIP, steps=48))) + " Z"
    hex_tile = "#{:02x}{:02x}{:02x}".format(*TILE[:3])
    hex_amber = "#{:02x}{:02x}{:02x}".format(*AMBER[:3])
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" role="img" aria-label="ArkAngel">\n'
        f'  <rect width="1024" height="1024" rx="{CORNER * 1024:.0f}" fill="{hex_tile}"/>\n'
        f'  <path d="{path}" fill="{hex_amber}"/>\n'
        f'  <circle cx="{PIN_CX * 1024:.0f}" cy="{PIN_CY * 1024:.0f}" r="{DOT_R * 1024:.1f}" fill="{hex_tile}"/>\n'
        f'  <ellipse cx="512" cy="{HALO_CY * 1024:.1f}" rx="{HALO_W / 2 * 1024:.1f}" ry="{HALO_H / 2 * 1024:.1f}" '
        f'fill="none" stroke="{hex_amber}" stroke-width="{_halo_stroke(1024) * 1024:.1f}"/>\n'
        "</svg>\n"
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ark-angel.svg").write_text(svg(), encoding="utf-8")

    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = {n: draw(n) for n in ico_sizes}
    frames[256].save(OUT / "ark-angel.ico", sizes=[(n, n) for n in ico_sizes],
                     append_images=[frames[n] for n in ico_sizes if n != 256])
    # Pillow builds the ICNS size set from the largest frame; give it a crisp 1024 master.
    draw(1024, supersample=4).save(OUT / "ark-angel.icns")
    draw(512).save(OUT / "ark-angel-512.png")
    draw(32).save(ROOT / "src" / "ark_angel" / "report" / "favicon.png", optimize=True)
    print(f"wrote icons to {OUT.relative_to(ROOT)} and the report favicon")


if __name__ == "__main__":
    main()
