"""Pillow engine: EXIF handling must not rotate already-transposed pixels a second time."""

from __future__ import annotations

import threading

import pytest
from PIL import Image

from ezconverter.engines.base import ExecutionContext, InputInfo
from ezconverter.engines.images import PillowEngine
from ezconverter.sandbox import Limits

ORIENTATION = 0x0112
MAKE = 0x010F  # a tag that must survive


def _source(path, fmt: str, orientation: int, frames: int = 1):
    ims = [Image.new("RGB", (40, 20), (200 - 40 * i, 30, 30)) for i in range(frames)]
    ex = Image.Exif()
    ex[ORIENTATION] = orientation
    ex[MAKE] = "EZTestCam"
    kw = {"exif": ex.tobytes()}
    if frames > 1:
        kw.update(save_all=True, append_images=ims[1:])
    ims[0].save(path, fmt, **kw)
    return path


def _convert(tmp_path, src, source: str, target: str, **options):
    eng = PillowEngine()
    cap = next(c for c in eng.capabilities() if c.source == source and c.target == target)
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    ctx = ExecutionContext(
        capability=cap,
        input=InputInfo(src, src.name, source, src.stat().st_size),
        options=eng.validate_options(cap, options),
        output_dir=out_dir,
        work_dir=tmp_path,
        output_stem="o",
        limits=Limits(),
        cancel=threading.Event(),
    )
    return eng.execute(ctx).output_path


@pytest.mark.parametrize("target", ["jpeg", "png", "webp", "tiff"])
def test_transposed_pixels_do_not_keep_rotating_orientation_tag(tmp_path, target):
    src = _source(tmp_path / "in.jpg", "JPEG", orientation=6)
    out = _convert(tmp_path, src, "jpeg", target)
    with Image.open(out) as im:
        assert im.size == (20, 40)  # pixels were physically rotated
        ex = im.getexif()
        assert ex.get(ORIENTATION, 1) == 1
        assert ex.get(MAKE) == "EZTestCam"  # the rest of the EXIF is still preserved


def test_orientation_tag_is_untouched_when_pixels_are_not_transposed(tmp_path):
    # Multi-frame input is never transposed, so the tag (and the unrotated pixels) stay as they were.
    src = _source(tmp_path / "in.png", "PNG", orientation=6, frames=2)
    out = _convert(tmp_path, src, "png", "webp")
    with Image.open(out) as im:
        assert im.n_frames == 2 and im.size == (40, 20)
        assert im.getexif().get(ORIENTATION) == 6


def test_keep_metadata_off_drops_exif(tmp_path):
    src = _source(tmp_path / "in.jpg", "JPEG", orientation=6)
    out = _convert(tmp_path, src, "jpeg", "jpeg", keep_metadata=False)
    with Image.open(out) as im:
        assert im.size == (20, 40) and not im.getexif().get(MAKE)
