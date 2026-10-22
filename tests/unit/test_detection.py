import pytest
from fixtures.generate import corrupt

from ezconverter.detection import inspect_file

FORMATS = [
    "png",
    "jpeg",
    "webp",
    "avif",
    "gif",
    "tiff",
    "bmp",
    "ico",
    "svg",
    "mp3",
    "wav",
    "flac",
    "aac",
    "m4a",
    "ogg",
    "opus",
    "aiff",
    "mp4",
    "mkv",
    "mov",
    "avi",
    "webm",
    "mpeg",
    "pdf",
    "docx",
    "doc",
    "odt",
    "rtf",
    "xlsx",
    "xls",
    "ods",
    "pptx",
    "ppt",
    "odp",
    "zip",
    "tar",
    "tgz",
    "gz",
    "7z",
    "json",
    "xml",
    "yaml",
    "csv",
    "html",
    "md",
    "txt",
]


@pytest.mark.parametrize("fmt", FORMATS)
def test_detects_real_files_by_content(fx, fmt):
    p = fx(fmt)
    d = inspect_file(str(p), p.name)
    assert d.format == fmt, d.to_dict()
    assert not d.damaged and d.rejected_reason is None


def test_extension_is_never_trusted(fx, tmp_path):
    png = fx("png")
    spoof = tmp_path / "holiday.jpg"
    spoof.write_bytes(png.read_bytes())
    d = inspect_file(str(spoof), "holiday.jpg")
    assert d.format == "png" and d.extension_mismatch and d.extension_format == "jpeg"
    text = tmp_path / "evil.png"
    text.write_text("just some text, not an image\n")
    d = inspect_file(str(text), "evil.png")
    assert d.format == "txt" and d.extension_mismatch


def test_empty_and_unknown_files(tmp_path):
    e = tmp_path / "e.bin"
    e.write_bytes(b"")
    assert inspect_file(str(e), "e.bin").rejected_reason
    u = tmp_path / "u.bin"
    u.write_bytes(bytes(range(256)) * 10)
    d = inspect_file(str(u), "u.bin")
    assert d.format is None and d.confidence == "low"


@pytest.mark.parametrize("fmt", ["png", "jpeg", "gif", "webp", "tiff", "bmp", "pdf"])
def test_garbage_after_valid_magic_is_not_trusted(tmp_path, fmt):
    d = inspect_file(str(corrupt(fmt, tmp_path / "bad.bin")), "bad.bin")
    assert d.format is None or d.damaged or d.format == "txt", d.to_dict()


def test_damaged_zip_container(tmp_path, fx):
    p = tmp_path / "x.docx"
    raw = fx("docx").read_bytes()
    p.write_bytes(raw[: len(raw) // 2])
    d = inspect_file(str(p), "x.docx")
    assert d.damaged and d.format is None


def test_json_validity_matters(tmp_path):
    good = tmp_path / "a.txt"
    good.write_text('{"a": [1,2,3]}')
    assert inspect_file(str(good), "a.txt").format == "json"
    bad = tmp_path / "b.json"
    bad.write_text('{"a": [1,2,3}')
    assert inspect_file(str(bad), "b.json").format == "txt"


def test_image_bomb_is_flagged_not_decoded(tmp_path):
    from PIL import Image

    p = tmp_path / "bomb.png"
    Image.new("1", (30000, 30000)).save(p)  # 900 MP, a few KB on disk
    d = inspect_file(str(p), "bomb.png")
    assert d.rejected_reason and "safe" in d.rejected_reason


def test_media_details_exposed(fx):
    p = fx("mp4")
    d = inspect_file(str(p), "v.mp4")
    types = {s["codec_type"] for s in d.details["media"]["streams"]}
    assert {"video", "audio"} <= types and 1.5 < d.details["media"]["duration_seconds"] < 2.6
