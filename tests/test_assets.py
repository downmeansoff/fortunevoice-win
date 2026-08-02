"""The generated icon.

The .ico is what Explorer, the taskbar and Alt-Tab draw. Windows picks the
frame closest to the size it wants and scales it, so a missing frame does not
fail — it just looks blurry, which nobody files a bug about and everybody
notices.
"""

from __future__ import annotations

from PIL import Image

from fortunevoice import assets

WANTED = {(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48),
          (64, 64), (128, 128), (256, 256)}


def test_ico_contains_every_size(tmp_path):
    """Regression: built from the 16 px render, Pillow silently dropped every
    larger frame — its ICO writer discards requested sizes bigger than the
    image being saved — and shipped a one-frame icon that Explorer upscaled."""
    path = assets.write_ico(tmp_path / "icon.ico")
    with Image.open(path) as image:
        assert set(image.ico.sizes()) == WANTED


def test_ico_frames_are_not_upscaled(tmp_path):
    """Each frame must be its own render, not a blown-up small one. A frame
    that was upscaled has visibly fewer distinct colours than a native draw."""
    path = assets.write_ico(tmp_path / "icon.ico")
    with Image.open(path) as image:
        image.size = (256, 256)
        image.load()
        large = image.convert("RGBA")
    assert large.size == (256, 256)
    # An upscaled 16 px source cannot carry this much tonal variety.
    assert len(large.convert("L").getcolors(maxcolors=65536)) > 40


def test_mark_renders_at_the_requested_size():
    for size in (16, 32, 256):
        assert assets.mark(size, assets.IDLE).size == (size, size)


def test_tray_image_has_no_plate():
    """The tray icon sits directly on the user's taskbar colour, so its corners
    must be transparent — a plate of any kind would show as a box on a light
    taskbar."""
    image = assets.tray_image(assets.IDLE)
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((image.width - 1, 0))[3] == 0


def test_logo_is_opaque_and_blue():
    """The app icon is the product logo, not the tray silhouette: a filled
    gradient tile matching the identity tile in the window's own sidebar. An
    icon that doesn't match the app it opens looks like the wrong shortcut."""
    image = assets.logo(64).convert("RGBA")
    centre_top = image.getpixel((32, 6))
    assert centre_top[3] == 255, "the tile must be opaque"
    red, green, blue = centre_top[:3]
    assert blue > red + 40 and blue > green + 20, f"expected blue, got {centre_top}"
    # Corners are rounded, so they are transparent.
    assert image.getpixel((0, 0))[3] < 40


def test_logo_has_a_gradient():
    """Flat colour reads as printed ink; the gradient is what makes it a
    surface."""
    image = assets.logo(128).convert("RGBA")
    top = image.getpixel((64, 4))[:3]
    bottom = image.getpixel((64, 123))[:3]
    assert sum(top) > sum(bottom) + 60, f"{top} vs {bottom}"


def test_level_changes_the_mark():
    """The tray icon shows that audio is arriving; if level did nothing, a dead
    microphone would look identical to a working one."""
    quiet = assets.tray_image(assets.RECORDING, level=0.0)
    loud = assets.tray_image(assets.RECORDING, level=1.0)
    assert quiet.tobytes() != loud.tobytes()


def test_icon_path_generates_when_missing(tmp_path, monkeypatch):
    path = assets.icon_path()
    assert path.exists()
    assert path.suffix == ".ico"
