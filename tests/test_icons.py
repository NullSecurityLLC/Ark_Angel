"""The shipped icon files exist, carry the sizes each platform asks for, and match the build script."""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "packaging" / "icons"


def test_windows_icon_has_taskbar_through_large_sizes() -> None:
    with Image.open(ICONS / "ark-angel.ico") as ico:
        assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= set(ico.info["sizes"])


def test_macos_icon_and_png_sizes() -> None:
    with Image.open(ICONS / "ark-angel.icns") as icns:
        assert (512, 512, 2) in icns.info["sizes"]  # 1024 px Retina
    with Image.open(ICONS / "ark-angel-512.png") as png:
        assert png.size == (512, 512)
    with Image.open(ROOT / "src" / "ark_angel" / "report" / "favicon.png") as fav:
        assert fav.size == (32, 32)


def test_icon_uses_the_report_palette() -> None:
    with Image.open(ICONS / "ark-angel-512.png") as png:
        rgba = png.convert("RGBA")
        assert rgba.getpixel((256, 380))[:3] == (224, 178, 92)  # pin body below the dot: report amber #e0b25c
        assert rgba.getpixel((256, 272))[:3] == (20, 18, 14)  # the dot in the pin is cut out to the tile colour
        assert rgba.getpixel((40, 256))[:3] == (20, 18, 14)  # tile: report background #14120e
        assert rgba.getpixel((0, 0))[3] == 0  # rounded corners are transparent
    assert "#e0b25c" in (ICONS / "ark-angel.svg").read_text(encoding="utf-8")
