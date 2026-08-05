"""The app icon, drawn in code rather than shipped as a binary blob.

One generator produces every size the app needs — the multi-resolution .ico
for the shortcut, the taskbar and Alt-Tab, and the per-state bitmaps for the
tray. Generating it means the icon is reviewable in a diff and regenerates if
the palette changes, instead of being a file nobody can edit.

Two different marks, deliberately:

* **The app icon** is the product logo: a blue gradient tile with a white
  microphone, matching the identity tile in the window's own sidebar. An icon
  that doesn't match the app it opens looks like the wrong shortcut.
* **The tray icon** is a monochrome silhouette on a transparent background,
  tinted by state. It sits directly on the user's taskbar colour, so a plate
  of any kind would show as a box; and at 16 px a two-tone glyph turns to
  mush.
"""

from __future__ import annotations

from pathlib import Path

# Tray state colours. Idle is deliberately low-contrast: the tray is a place
# the user should be able to ignore until something is happening. Warm greys,
# so the icon belongs to the same palette as the windows.
IDLE = (169, 164, 154)
LOADING = (124, 118, 108)
RECORDING = (217, 83, 79)
PROCESSING = (212, 162, 127)
ERROR = (235, 219, 188)

# The logo gradient, top to bottom. Clay, passing through theme.ACCENT at the
# midpoint.
_TILE_TOP = (232, 148, 116)
_TILE_BOTTOM = (193, 95, 60)


def _microphone(draw, px: int, fill: tuple, stroke: float = 0.062) -> None:
    """The mic silhouette, drawn into a box of side `px`.

    Proportions are tuned for 16 px first: the capsule clears the cradle so
    the two shapes don't merge into a blob, and the cradle is an arc plus a
    stem plus a foot, which is the silhouette everyone reads as "microphone"
    without resolving any detail.
    """
    width = max(1, int(px * stroke))
    centre = px / 2

    capsule_w = px * 0.22
    capsule_top = px * 0.20
    capsule_bottom = px * 0.545
    draw.rounded_rectangle(
        (centre - capsule_w / 2, capsule_top, centre + capsule_w / 2, capsule_bottom),
        radius=capsule_w / 2, fill=fill,
    )

    cradle_w = px * 0.44
    cradle_top = px * 0.43
    cradle_bottom = px * 0.72
    draw.arc(
        (centre - cradle_w / 2, cradle_top, centre + cradle_w / 2, cradle_bottom),
        start=0, end=180, fill=fill, width=width,
    )
    stem_top = (cradle_top + cradle_bottom) / 2
    draw.line((centre, stem_top, centre, px * 0.82), fill=fill, width=width)
    foot = px * 0.12
    draw.line((centre - foot, px * 0.82, centre + foot, px * 0.82), fill=fill, width=width)


def logo(size: int):
    """The product icon: white microphone on a blue gradient tile.

    Drawn at 8x and downsampled, because PIL has no antialiased primitives and
    a 32 px icon drawn directly looks like gravel.
    """
    from PIL import Image, ImageDraw

    scale = 8
    px = size * scale
    radius = int(px * 0.225)  # close to the Windows 11 app-icon curve

    # Vertical gradient, then masked to the rounded square.
    gradient = Image.new("RGB", (1, px))
    for y in range(px):
        t = y / max(1, px - 1)
        gradient.putpixel((0, y), tuple(
            int(_TILE_TOP[i] + (_TILE_BOTTOM[i] - _TILE_TOP[i]) * t) for i in range(3)
        ))
    tile = gradient.resize((px, px)).convert("RGBA")

    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, px - 1, px - 1), radius=radius, fill=255)
    tile.putalpha(mask)

    draw = ImageDraw.Draw(tile)
    # A hairline of lighter blue along the top edge: without it the tile reads
    # as flat printed colour rather than as a surface.
    draw.rounded_rectangle((0, 0, px - 1, px - 1), radius=radius,
                           outline=(255, 255, 255, 46), width=max(1, int(px * 0.012)))
    _microphone(draw, px, (255, 255, 255, 255), stroke=0.058)

    return tile.resize((size, size), Image.LANCZOS)


def tray_image(colour: tuple[int, int, int], level: float = 0.0):
    """Monochrome silhouette for the notification area, tinted by state.

    `level` (0…1) raises the bars either side, so the tray shows that audio is
    actually arriving — a dead microphone should not look like a working one.
    """
    from PIL import Image, ImageDraw

    size, scale = 64, 8
    px = size * scale
    image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    fill = colour + (255,)

    _microphone(draw, px, fill, stroke=0.058)

    bar_w = max(1, int(px * 0.05))
    centre = px / 2
    capsule_mid = px * 0.37
    for side in (-1, 1):
        x = centre + side * px * 0.34
        height = px * (0.12 + 0.26 * min(1.0, max(0.0, level)))
        draw.line((x, capsule_mid - height / 2, x, capsule_mid + height / 2),
                  fill=fill, width=bar_w)

    return image.resize((size, size), Image.LANCZOS)


def write_ico(path: Path) -> Path:
    """Multi-resolution .ico for the shortcut, taskbar and Alt-Tab.

    All the sizes Windows actually asks for. Pillow's ICO writer silently
    DISCARDS any requested size larger than the image being saved, so the
    largest frame has to be the base — building from the 16 px render produced
    a one-frame icon that Explorer upscaled, with no error anywhere.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    images = [logo(s) for s in sizes]
    base, rest = images[-1], images[:-1]
    base.save(path, format="ICO", sizes=[(s, s) for s in sizes], append_images=rest)
    return path


def icon_path() -> Path:
    """The .ico shipped with the source tree, generated on demand if absent."""
    path = Path(__file__).resolve().parent.parent.parent / "assets" / "fortunevoice.ico"
    if not path.exists():
        write_ico(path)
    return path


# Kept for callers that want the mark on a plate at an arbitrary tint (the
# window sidebar draws its own via ui.icons.tile).
def mark(size: int, colour: tuple[int, int, int], tile: bool = True, level: float = 0.0):
    return logo(size) if tile else tray_image(colour, level)


if __name__ == "__main__":  # python -m fortunevoice.assets → regenerate
    print(write_ico(icon_path()))
