"""Glyphs, drawn in code.

The macOS build uses SF Symbols, which have no Windows equivalent worth
relying on: Segoe Fluent Icons only exists on Windows 11 and addresses its
glyphs through private-use codepoints that move between releases, and colour
emoji clash with a flat blue accent.

So the dozen glyphs this UI needs are drawn from primitives — every one is a
few arcs and lines. They render at 8x and downsample, because PIL has no
antialiased drawing and a 16 px glyph drawn directly looks like gravel.

`PhotoImage` references must be held by the caller: Tk does not own the image,
and a garbage-collected one silently becomes a blank square.
"""

from __future__ import annotations

from functools import lru_cache

SCALE = 8


def _new(size: int):
    from PIL import Image, ImageDraw

    px = size * SCALE
    image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image), px


def _rgba(colour: str, alpha: int = 255) -> tuple:
    colour = colour.lstrip("#")
    return (int(colour[0:2], 16), int(colour[2:4], 16), int(colour[4:6], 16), alpha)


# ── individual glyphs, each drawing into a unit box of side `px` ─────────


def _clock(draw, px, fill, w):
    draw.ellipse((w, w, px - w, px - w), outline=fill, width=w)
    draw.line((px / 2, px / 2, px / 2, px * 0.26), fill=fill, width=w)
    draw.line((px / 2, px / 2, px * 0.70, px * 0.58), fill=fill, width=w)


def _chart(draw, px, fill, w):
    for i, height in enumerate((0.34, 0.62, 0.46)):
        x = px * (0.22 + i * 0.28)
        draw.rounded_rectangle((x - w, px * (0.82 - height), x + w, px * 0.82),
                               radius=w, fill=fill)


def _book(draw, px, fill, w):
    draw.rounded_rectangle((px * 0.18, px * 0.16, px * 0.82, px * 0.84),
                           radius=px * 0.10, outline=fill, width=w)
    draw.line((px * 0.38, px * 0.16, px * 0.38, px * 0.84), fill=fill, width=w)


def _gear(draw, px, fill, w):
    import math

    centre = px / 2
    # A ring with short stubby teeth. Long thin spokes on a small circle read
    # as a sun, not a gear, which is what the first attempt looked like.
    draw.ellipse((px * 0.22, px * 0.22, px * 0.78, px * 0.78), outline=fill, width=w)
    draw.ellipse((px * 0.42, px * 0.42, px * 0.58, px * 0.58), outline=fill, width=w)
    for index in range(8):
        angle = index * math.pi / 4
        inner, outer = px * 0.26, px * 0.40
        draw.line(
            (centre + math.cos(angle) * inner, centre + math.sin(angle) * inner,
             centre + math.cos(angle) * outer, centre + math.sin(angle) * outer),
            fill=fill, width=int(w * 1.9),
        )


def _bolt(draw, px, fill, w):  # noqa: ARG001
    draw.polygon(
        [(px * 0.58, px * 0.12), (px * 0.30, px * 0.55), (px * 0.47, px * 0.55),
         (px * 0.42, px * 0.88), (px * 0.70, px * 0.45), (px * 0.53, px * 0.45)],
        fill=fill,
    )


def _sparkle(draw, px, fill, w):
    def star(cx, cy, r):
        draw.polygon([(cx, cy - r), (cx + r * 0.28, cy - r * 0.28), (cx + r, cy),
                      (cx + r * 0.28, cy + r * 0.28), (cx, cy + r),
                      (cx - r * 0.28, cy + r * 0.28), (cx - r, cy),
                      (cx - r * 0.28, cy - r * 0.28)], fill=fill)
    star(px * 0.44, px * 0.42, px * 0.30)
    star(px * 0.74, px * 0.74, px * 0.15)


def _wand(draw, px, fill, w):
    draw.line((px * 0.22, px * 0.80, px * 0.72, px * 0.28), fill=fill, width=w)
    draw.ellipse((px * 0.66, px * 0.16, px * 0.86, px * 0.36), fill=fill)


def _power(draw, px, fill, w):
    draw.arc((px * 0.20, px * 0.22, px * 0.80, px * 0.82), start=305, end=235,
             fill=fill, width=w)
    draw.line((px / 2, px * 0.14, px / 2, px * 0.46), fill=fill, width=w)


def _speaker(draw, px, fill, w):
    draw.polygon([(px * 0.20, px * 0.40), (px * 0.36, px * 0.40), (px * 0.54, px * 0.22),
                  (px * 0.54, px * 0.78), (px * 0.36, px * 0.60), (px * 0.20, px * 0.60)],
                 fill=fill)
    draw.arc((px * 0.50, px * 0.30, px * 0.82, px * 0.70), start=300, end=60,
             fill=fill, width=w)


def _mic(draw, px, fill, w):
    draw.rounded_rectangle((px * 0.40, px * 0.16, px * 0.60, px * 0.54),
                           radius=px * 0.10, fill=fill)
    draw.arc((px * 0.28, px * 0.38, px * 0.72, px * 0.72), start=0, end=180,
             fill=fill, width=w)
    draw.line((px / 2, px * 0.55, px / 2, px * 0.84), fill=fill, width=w)


def _keyboard(draw, px, fill, w):
    draw.rounded_rectangle((px * 0.12, px * 0.28, px * 0.88, px * 0.72),
                           radius=px * 0.10, outline=fill, width=w)
    for x in (0.26, 0.40, 0.54, 0.68):
        draw.line((px * x, px * 0.44, px * x, px * 0.46), fill=fill, width=w)
    draw.line((px * 0.34, px * 0.60, px * 0.66, px * 0.60), fill=fill, width=w)


def _globe(draw, px, fill, w):
    draw.ellipse((px * 0.14, px * 0.14, px * 0.86, px * 0.86), outline=fill, width=w)
    draw.ellipse((px * 0.38, px * 0.14, px * 0.62, px * 0.86), outline=fill, width=w)
    draw.line((px * 0.16, px * 0.38, px * 0.84, px * 0.38), fill=fill, width=w)
    draw.line((px * 0.16, px * 0.62, px * 0.84, px * 0.62), fill=fill, width=w)


def _clipboard(draw, px, fill, w):
    draw.rounded_rectangle((px * 0.24, px * 0.20, px * 0.76, px * 0.86),
                           radius=px * 0.10, outline=fill, width=w)
    draw.rounded_rectangle((px * 0.38, px * 0.10, px * 0.62, px * 0.26),
                           radius=px * 0.06, fill=fill)


def _chip(draw, px, fill, w):
    draw.rounded_rectangle((px * 0.26, px * 0.26, px * 0.74, px * 0.74),
                           radius=px * 0.08, outline=fill, width=w)
    for offset in (0.38, 0.50, 0.62):
        draw.line((px * offset, px * 0.10, px * offset, px * 0.24), fill=fill, width=w)
        draw.line((px * offset, px * 0.76, px * offset, px * 0.90), fill=fill, width=w)
        draw.line((px * 0.10, px * offset, px * 0.24, px * offset), fill=fill, width=w)
        draw.line((px * 0.76, px * offset, px * 0.90, px * offset), fill=fill, width=w)


def _tap(draw, px, fill, w):
    draw.ellipse((px * 0.36, px * 0.14, px * 0.64, px * 0.42), outline=fill, width=w)
    draw.arc((px * 0.18, px * 0.30, px * 0.82, px * 0.94), start=200, end=340,
             fill=fill, width=w)
    draw.line((px * 0.50, px * 0.44, px * 0.50, px * 0.72), fill=fill, width=w)


def _search(draw, px, fill, w):
    draw.ellipse((px * 0.16, px * 0.16, px * 0.66, px * 0.66), outline=fill, width=w)
    draw.line((px * 0.62, px * 0.62, px * 0.86, px * 0.86), fill=fill, width=w)


def _trash(draw, px, fill, w):
    draw.line((px * 0.18, px * 0.26, px * 0.82, px * 0.26), fill=fill, width=w)
    draw.rounded_rectangle((px * 0.26, px * 0.26, px * 0.74, px * 0.86),
                           radius=px * 0.08, outline=fill, width=w)
    draw.line((px * 0.40, px * 0.16, px * 0.60, px * 0.16), fill=fill, width=w)


def _share(draw, px, fill, w):
    draw.line((px * 0.50, px * 0.14, px * 0.50, px * 0.60), fill=fill, width=w)
    draw.line((px * 0.50, px * 0.14, px * 0.34, px * 0.30), fill=fill, width=w)
    draw.line((px * 0.50, px * 0.14, px * 0.66, px * 0.30), fill=fill, width=w)
    draw.arc((px * 0.20, px * 0.36, px * 0.80, px * 0.96), start=180, end=360,
             fill=fill, width=w)


def _chevron(draw, px, fill, w):
    draw.line((px * 0.34, px * 0.40, px * 0.50, px * 0.24), fill=fill, width=w)
    draw.line((px * 0.50, px * 0.24, px * 0.66, px * 0.40), fill=fill, width=w)
    draw.line((px * 0.34, px * 0.60, px * 0.50, px * 0.76), fill=fill, width=w)
    draw.line((px * 0.50, px * 0.76, px * 0.66, px * 0.60), fill=fill, width=w)


def _close(draw, px, fill, w):
    draw.ellipse((px * 0.10, px * 0.10, px * 0.90, px * 0.90), fill=fill)


GLYPHS = {
    "clock": _clock, "chart": _chart, "book": _book, "gear": _gear,
    "bolt": _bolt, "sparkle": _sparkle, "wand": _wand, "power": _power,
    "speaker": _speaker, "mic": _mic, "keyboard": _keyboard, "globe": _globe,
    "clipboard": _clipboard, "chip": _chip, "tap": _tap, "search": _search,
    "trash": _trash, "share": _share, "chevron": _chevron, "close": _close,
}


@lru_cache(maxsize=256)
def _render(name: str, size: int, colour: str, stroke: float):
    from PIL import Image

    image, draw, px = _new(size)
    fill = _rgba(colour)
    width = max(1, int(px * stroke))
    GLYPHS[name](draw, px, fill, width)
    return image.resize((size, size), Image.LANCZOS)


def image(name: str, size: int = 16, colour: str = "#F5F4EE", stroke: float = 0.075):
    """PIL image of a glyph. Cached — the same icon is asked for on every
    repaint of a scrolling list."""
    return _render(name, size, colour, stroke)


@lru_cache(maxsize=256)
def tile(name: str, size: int, glyph_colour: str, background: str, radius: float = 0.28):
    """A glyph on a rounded colour plate — the Settings row icons."""
    from PIL import Image, ImageDraw

    plate = Image.new("RGBA", (size * SCALE, size * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(plate)
    draw.rounded_rectangle((0, 0, size * SCALE - 1, size * SCALE - 1),
                           radius=int(size * SCALE * radius), fill=_rgba(background))
    plate = plate.resize((size, size), Image.LANCZOS)
    glyph = image(name, int(size * 0.62), glyph_colour, stroke=0.09)
    offset = (size - glyph.width) // 2
    plate.alpha_composite(glyph, (offset, offset))
    return plate


def photo(pil_image):
    """PIL image → Tk PhotoImage. The caller MUST keep the reference alive."""
    from PIL import ImageTk

    return ImageTk.PhotoImage(pil_image)
