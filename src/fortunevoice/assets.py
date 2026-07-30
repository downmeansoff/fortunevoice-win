"""The app's icon, drawn in code rather than shipped as a binary blob.

One generator produces every size the app needs — the multi-resolution .ico
for the shortcut and the taskbar, and the per-state bitmaps for the tray. The
mark is a microphone capsule with a level bar either side, which reads at 16 px
where a detailed glyph turns to mush.

Generating it means the icon is reviewable in a diff and regenerates if the
palette changes, instead of being a file nobody can edit.
"""

from __future__ import annotations

from pathlib import Path

# Tray state colours. The idle mark is deliberately low-contrast: the tray is
# a place the user should be able to ignore until something is happening.
IDLE = (150, 158, 178)
LOADING = (120, 126, 145)
RECORDING = (255, 77, 94)
PROCESSING = (255, 176, 32)
ERROR = (255, 214, 79)

_TILE = (18, 20, 27)
_TILE_EDGE = (44, 49, 63)


def mark(size: int, colour: tuple[int, int, int], tile: bool = True, level: float = 0.0):
    """The FortuneVoice mark at `size` px.

    `tile` draws the rounded dark plate behind it — right for a desktop icon,
    wrong for a tray icon, which must sit directly on the user's taskbar
    colour. `level` (0…1) raises the side bars, so the tray icon can show that
    audio is actually arriving.
    """
    from PIL import Image, ImageDraw

    # Draw at 8x and downsample: PIL has no antialiased primitives, and a
    # 16 px icon drawn directly looks like a broken QR code.
    scale = 8
    px = size * scale
    image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    if tile:
        radius = int(px * 0.22)
        draw.rounded_rectangle((0, 0, px - 1, px - 1), radius=radius,
                               fill=_TILE + (255,), outline=_TILE_EDGE + (255,),
                               width=max(1, int(px * 0.012)))

    fill = colour + (255,)
    centre = px / 2
    stroke = max(1, int(px * 0.058))

    # Microphone capsule. Kept clear of the cradle below it — at 16 px an
    # overlap turns the two shapes into one blob.
    capsule_w = px * 0.21
    capsule_top = px * 0.19
    capsule_bottom = px * 0.53
    draw.rounded_rectangle(
        (centre - capsule_w / 2, capsule_top, centre + capsule_w / 2, capsule_bottom),
        radius=capsule_w / 2, fill=fill,
    )

    # Cradle arc plus stem and foot: the silhouette everyone reads as
    # "microphone" without needing to resolve any detail.
    cradle_w = px * 0.46
    cradle_top = px * 0.42
    cradle_bottom = px * 0.72
    draw.arc(
        (centre - cradle_w / 2, cradle_top, centre + cradle_w / 2, cradle_bottom),
        start=0, end=180, fill=fill, width=stroke,
    )
    stem_top = (cradle_top + cradle_bottom) / 2
    draw.line((centre, stem_top, centre, px * 0.83), fill=fill, width=stroke)
    foot = px * 0.13
    draw.line((centre - foot, px * 0.83, centre + foot, px * 0.83), fill=fill, width=stroke)

    # Level bars, centred on the capsule. At rest they are short ticks that
    # balance the mark; `level` grows them symmetrically, which is what makes
    # the tray icon show that audio is actually arriving.
    capsule_mid = (capsule_top + capsule_bottom) / 2
    for side in (-1, 1):
        x = centre + side * px * 0.35
        height = px * (0.12 + 0.26 * min(1.0, max(0.0, level)))
        draw.line((x, capsule_mid - height / 2, x, capsule_mid + height / 2),
                  fill=fill, width=stroke)

    return image.resize((size, size), Image.LANCZOS)


def tray_image(colour: tuple[int, int, int], level: float = 0.0):
    """64 px, no plate — Windows scales tray icons down from this."""
    return mark(64, colour, tile=False, level=level)


def write_ico(path: Path) -> Path:
    """Multi-resolution .ico for the shortcut, taskbar and Alt-Tab.

    All the sizes Windows actually asks for. Missing one makes Explorer scale
    the nearest, which is where blurry desktop icons come from.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    images = [mark(s, IDLE, tile=True) for s in sizes]
    # The LARGEST image has to be the base: Pillow's ICO writer silently drops
    # any requested size bigger than the image it is saving, so building from
    # the 16 px render produced a one-frame icon and a blurry desktop.
    base, rest = images[-1], images[:-1]
    base.save(path, format="ICO", sizes=[(s, s) for s in sizes], append_images=rest)
    return path


def icon_path() -> Path:
    """The .ico shipped with the source tree, generated on demand if absent."""
    path = Path(__file__).resolve().parent.parent.parent / "assets" / "fortunevoice.ico"
    if not path.exists():
        write_ico(path)
    return path


if __name__ == "__main__":  # python -m fortunevoice.assets → regenerate
    print(write_ico(icon_path()))
