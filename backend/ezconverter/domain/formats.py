"""Canonical format identifiers.

A format id is the stable, lowercase identifier used by every other layer (detection,
registry, planner, API, clients). Extensions and MIME types are only hints.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Family(str, Enum):
    DOCUMENT = "document"
    SPREADSHEET = "spreadsheet"
    PRESENTATION = "presentation"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    ARCHIVE = "archive"
    DATA = "data"


@dataclass(frozen=True)
class Format:
    id: str
    name: str
    family: Family
    mime: str
    extensions: tuple[str, ...]

    @property
    def primary_extension(self) -> str:
        return self.extensions[0]


def _f(id_: str, name: str, family: Family, mime: str, *exts: str) -> Format:
    return Format(id_, name, family, mime, exts or (id_,))


_D, _S, _P = Family.DOCUMENT, Family.SPREADSHEET, Family.PRESENTATION
_I, _A, _V, _Z, _T = Family.IMAGE, Family.AUDIO, Family.VIDEO, Family.ARCHIVE, Family.DATA

_ALL = [
    # documents
    _f("pdf", "PDF", _D, "application/pdf"),
    _f("docx", "Word (DOCX)", _D, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    _f("doc", "Word 97-2003 (DOC)", _D, "application/msword"),
    _f("odt", "OpenDocument Text", _D, "application/vnd.oasis.opendocument.text"),
    _f("rtf", "Rich Text Format", _D, "application/rtf"),
    _f("txt", "Plain text", _D, "text/plain", "txt", "text"),
    _f("html", "HTML", _D, "text/html", "html", "htm"),
    _f("md", "Markdown", _D, "text/markdown", "md", "markdown"),
    # spreadsheets
    _f("xlsx", "Excel (XLSX)", _S, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    _f("xls", "Excel 97-2003 (XLS)", _S, "application/vnd.ms-excel"),
    _f("ods", "OpenDocument Spreadsheet", _S, "application/vnd.oasis.opendocument.spreadsheet"),
    # presentations
    _f(
        "pptx",
        "PowerPoint (PPTX)",
        _P,
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
    _f("ppt", "PowerPoint 97-2003 (PPT)", _P, "application/vnd.ms-powerpoint"),
    _f("odp", "OpenDocument Presentation", _P, "application/vnd.oasis.opendocument.presentation"),
    # images
    _f("png", "PNG", _I, "image/png"),
    _f("jpeg", "JPEG", _I, "image/jpeg", "jpg", "jpeg", "jpe"),
    _f("webp", "WebP", _I, "image/webp"),
    _f("avif", "AVIF", _I, "image/avif"),
    _f("gif", "GIF", _I, "image/gif"),
    _f("tiff", "TIFF", _I, "image/tiff", "tiff", "tif"),
    _f("bmp", "BMP", _I, "image/bmp"),
    _f("svg", "SVG", _I, "image/svg+xml"),
    _f("ico", "ICO", _I, "image/vnd.microsoft.icon"),
    # audio
    _f("mp3", "MP3", _A, "audio/mpeg"),
    _f("wav", "WAV", _A, "audio/wav", "wav", "wave"),
    _f("flac", "FLAC", _A, "audio/flac"),
    _f("aac", "AAC (ADTS)", _A, "audio/aac"),
    _f("m4a", "M4A (MP4 audio)", _A, "audio/mp4"),
    _f("ogg", "Ogg Vorbis", _A, "audio/ogg", "ogg", "oga"),
    _f("opus", "Opus", _A, "audio/opus"),
    _f("aiff", "AIFF", _A, "audio/aiff", "aiff", "aif", "aifc"),
    # video
    _f("mp4", "MP4", _V, "video/mp4", "mp4", "m4v"),
    _f("mkv", "Matroska (MKV)", _V, "video/x-matroska"),
    _f("mov", "QuickTime (MOV)", _V, "video/quicktime"),
    _f("avi", "AVI", _V, "video/x-msvideo"),
    _f("webm", "WebM", _V, "video/webm"),
    _f("mpeg", "MPEG program stream", _V, "video/mpeg", "mpeg", "mpg"),
    # archives
    _f("zip", "ZIP", _Z, "application/zip"),
    _f("tar", "TAR", _Z, "application/x-tar"),
    _f("tgz", "TAR + gzip", _Z, "application/gzip", "tar.gz", "tgz"),
    _f("gz", "gzip (single file)", _Z, "application/gzip", "gz"),
    _f("7z", "7-Zip", _Z, "application/x-7z-compressed"),
    # structured data
    _f("json", "JSON", _T, "application/json"),
    _f("xml", "XML", _T, "application/xml"),
    _f("yaml", "YAML", _T, "application/yaml", "yaml", "yml"),
    _f("csv", "CSV", _T, "text/csv"),
]

FORMATS: dict[str, Format] = {f.id: f for f in _ALL}

_EXT_INDEX: dict[str, str] = {}
for _fmt in _ALL:
    for _e in _fmt.extensions:
        _EXT_INDEX.setdefault(_e, _fmt.id)


def get_format(format_id: str) -> Format | None:
    return FORMATS.get(format_id)


def format_from_extension(filename: str) -> str | None:
    """Hint only. Returns a format id for the filename's extension, if known."""
    name = filename.lower()
    if name.endswith(".tar.gz"):
        return "tgz"
    if "." not in name:
        return None
    return _EXT_INDEX.get(name.rsplit(".", 1)[1])
