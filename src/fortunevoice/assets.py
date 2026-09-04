"""The app icon, drawn in code rather than shipped as a binary blob.

One generator produces every size the app needs: the multi-resolution .ico
for the shortcut, the taskbar and Alt-Tab, and the per-state bitmaps for the
tray. Generating it means the icon is reviewable in a diff and regenerates if
the palette changes, instead of being a file nobody can edit.

Two different marks, deliberately:

* **The app icon** is the product logo: a glass sphere. It has room to be
  itself at 48 px and up, which is every place Windows shows it: the desktop
  shortcut, the taskbar, Alt-Tab, the window corner.
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

# The sphere, top to bottom: slate crown, deeper middle, then the bright
# caustic where the light leaves the glass. Positions are fractions of the
# height.
_SPHERE_STOPS = (
    (0.00, (78, 116, 132)),
    (0.42, (58, 96, 112)),
    (0.86, (150, 196, 208)),
    (1.00, (214, 236, 240)),
)
_SPHERE_CREAM = (247, 240, 226)   # the reflection
_SPHERE_RIM = (22, 34, 40)        # the glass edge


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
    """The product icon: a glass sphere.

    Drawn rather than shipped as a bitmap, like everything else here: the
    shape is reviewable in a diff and regenerates at whatever size the OS asks
    for.

    The reflection has a hard lower edge on purpose. Blurring it turns the
    sphere into a glowing ball; a window reflected in glass has a boundary,
    and that boundary is most of what makes this read as glass at all.
    """
    from PIL import Image, ImageDraw, ImageFilter

    px = size * 4

    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, px - 1, px - 1), fill=255)

    column = Image.new("RGB", (1, px))
    paint = ImageDraw.Draw(column)
    for y in range(px):
        f = y / max(1, px - 1)
        for (a_pos, a_col), (b_pos, b_col) in zip(_SPHERE_STOPS, _SPHERE_STOPS[1:]):
            if a_pos <= f <= b_pos:
                k = (f - a_pos) / ((b_pos - a_pos) or 1)
                paint.point((0, y), fill=tuple(
                    round(a_col[i] + (b_col[i] - a_col[i]) * k) for i in range(3)))
                break
    image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    image.paste(column.resize((px, px)), (0, 0), mask)

    reflection = Image.new("L", (px, px), 0)
    ImageDraw.Draw(reflection).ellipse(
        (px * 0.17, px * 0.035, px * 0.83, px * 0.47), fill=255)
    reflection = reflection.filter(ImageFilter.GaussianBlur(px * 0.008))
    image.paste(Image.new("RGBA", (px, px), _SPHERE_CREAM + (255,)), (0, 0),
                Image.composite(reflection, Image.new("L", (px, px), 0), mask))

    # Thick glass darkens where you look through more of it: the rim.
    inner = Image.new("L", (px, px), 0)
    ImageDraw.Draw(inner).ellipse((0, 0, px - 1, px - 1), outline=255,
                                  width=int(px * 0.05))
    inner = inner.filter(ImageFilter.GaussianBlur(px * 0.02))
    image.paste(Image.new("RGBA", (px, px), (14, 26, 32, 255)), (0, 0),
                Image.composite(inner.point(lambda v: int(v * 0.45)),
                                Image.new("L", (px, px), 0), mask))

    ImageDraw.Draw(image).ellipse((1, 1, px - 2, px - 2),
                                  outline=_SPHERE_RIM + (225,),
                                  width=max(1, int(px * 0.013)))
    return image.resize((size, size), Image.LANCZOS)


def _sphere(px: int, tint: tuple[int, int, int] | None, strength: float,
            box: tuple[float, float, float, float]):
    """The glass sphere inside `box`, its body pulled towards `tint`.

    Shared by the app icon and the tray, so the two can never drift apart:
    an icon that does not match the app it opens looks like the wrong
    shortcut.
    """
    from PIL import Image, ImageDraw, ImageFilter

    left, top, right, bottom = box
    width, height = right - left, bottom - top

    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).ellipse((left, top, right, bottom), fill=255)

    def blend(colour):
        if tint is None or strength <= 0:
            return colour
        return tuple(round(c + (t - c) * strength) for c, t in zip(colour, tint))

    column = Image.new("RGB", (1, px))
    paint = ImageDraw.Draw(column)
    for y in range(px):
        f = min(1.0, max(0.0, (y - top) / (height or 1)))
        for (a_pos, a_col), (b_pos, b_col) in zip(_SPHERE_STOPS, _SPHERE_STOPS[1:]):
            if a_pos <= f <= b_pos:
                k = (f - a_pos) / ((b_pos - a_pos) or 1)
                mixed = tuple(round(a_col[i] + (b_col[i] - a_col[i]) * k) for i in range(3))
                paint.point((0, y), fill=blend(mixed))
                break
    image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    image.paste(column.resize((px, px)), (0, 0), mask)

    reflection = Image.new("L", (px, px), 0)
    ImageDraw.Draw(reflection).ellipse(
        (left + width * 0.17, top + height * 0.035,
         left + width * 0.83, top + height * 0.47), fill=255)
    reflection = reflection.filter(ImageFilter.GaussianBlur(px * 0.008))
    image.paste(Image.new("RGBA", (px, px), _SPHERE_CREAM + (255,)), (0, 0),
                Image.composite(reflection, Image.new("L", (px, px), 0), mask))

    inner = Image.new("L", (px, px), 0)
    ImageDraw.Draw(inner).ellipse((left, top, right, bottom), outline=255,
                                  width=int(width * 0.05))
    inner = inner.filter(ImageFilter.GaussianBlur(px * 0.02))
    image.paste(Image.new("RGBA", (px, px), (14, 26, 32, 255)), (0, 0),
                Image.composite(inner.point(lambda v: int(v * 0.45)),
                                Image.new("L", (px, px), 0), mask))

    ImageDraw.Draw(image).ellipse((left + 1, top + 1, right - 1, bottom - 1),
                                  outline=_SPHERE_RIM + (225,),
                                  width=max(1, int(width * 0.013)))
    return image


def tray_image(colour: tuple[int, int, int], level: float = 0.0):
    """The sphere in the notification area, tinted by state.

    The tray is a status light before it is a logo: at a glance it has to say
    idle, recording, or transcribing. So the body takes the state colour:
    idle keeps the natural glass, and the rest are pulled far enough for a
    16 px blob to read as red or amber rather than "the icon".

    The level bars stay. They sit outside the sphere, so they survive the size
    the sphere itself struggles at, and they are the only thing that says a
    dead microphone is dead.
    """
    from PIL import ImageDraw

    size, scale = 64, 8
    px = size * scale
    # Idle is the product's own colour; anything else is a signal and gets
    # pulled hard enough to be unmistakable at 16 px.
    strength = 0.0 if colour == IDLE else 0.72
    inset = px * 0.13
    image = _sphere(px, colour, strength, (inset, inset, px - inset, px - inset))

    draw = ImageDraw.Draw(image)
    fill = colour + (255,)
    bar_w = max(1, int(px * 0.05))
    centre = px / 2
    for side in (-1, 1):
        x = centre + side * px * 0.44
        height = px * (0.12 + 0.26 * min(1.0, max(0.0, level)))
        draw.line((x, centre - height / 2, x, centre + height / 2),
                  fill=fill, width=bar_w)

    from PIL import Image as _Image

    return image.resize((size, size), _Image.LANCZOS)


def write_ico(path: Path) -> Path:
    """Multi-resolution .ico for the shortcut, taskbar and Alt-Tab.

    All the sizes Windows actually asks for. Pillow's ICO writer silently
    DISCARDS any requested size larger than the image being saved, so the
    largest frame has to be the base: building from the 16 px render produced
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
