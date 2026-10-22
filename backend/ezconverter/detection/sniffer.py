"""Content-based format identification.

Identification never trusts the filename or client-supplied MIME type. Extensions are used only
to break ties between text formats that are indistinguishable by content (md/txt/yaml/csv).
This module parses untrusted bytes: call it inside the sandboxed worker (see workers/entry.py).
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from typing import Any

from ..domain.formats import FORMATS, format_from_extension
from ..sandbox import Limits, run_process, which

HEAD_BYTES = 64 * 1024
MAX_TEXT_PARSE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_PIXELS = 178_956_970 // 2  # ~89 MP; Pillow's own bomb threshold is ~179 MP
OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
_TEXT_TIEBREAK = frozenset({"txt", "md", "yaml", "csv", "json"})


@dataclass
class Detection:
    format: str | None
    confidence: str  # "high" | "medium" | "low"
    method: str
    mime: str | None = None
    extension_format: str | None = None
    extension_mismatch: bool = False
    damaged: bool = False
    rejected_reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "confidence": self.confidence,
            "method": self.method,
            "mime": self.mime,
            "extension_format": self.extension_format,
            "extension_mismatch": self.extension_mismatch,
            "damaged": self.damaged,
            "rejected_reason": self.rejected_reason,
            "details": self.details,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- helpers


def _scan_for(path: str, needles: list[bytes], limit: int = 64 * 1024 * 1024) -> set[bytes]:
    found: set[bytes] = set()
    overlap = max(len(n) for n in needles)
    carry = b""
    read = 0
    with open(path, "rb") as fh:
        while read < limit:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            read += len(chunk)
            buf = carry + chunk
            for n in needles:
                if n in buf:
                    found.add(n)
            carry = buf[-overlap:]
    return found


def _u16(s: str) -> bytes:
    return s.encode("utf-16-le")


def ffprobe(path: str, workdir: str) -> dict[str, Any] | None:
    exe = which("ffprobe")
    if not exe:
        return None
    r = run_process(
        [
            exe,
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            path,
        ],
        workdir=workdir,
        limits=Limits(wall_seconds=30, cpu_seconds=30, memory_mb=2048, isolate_network=False),
        sandboxed=False,  # we are already inside the worker sandbox
    )
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def _summarize_probe(probe: dict[str, Any]) -> dict[str, Any]:
    fmt = probe.get("format", {})
    streams = []
    for s in probe.get("streams", []):
        entry = {
            k: s.get(k)
            for k in (
                "index",
                "codec_type",
                "codec_name",
                "width",
                "height",
                "sample_rate",
                "channels",
                "avg_frame_rate",
            )
            if s.get(k) is not None
        }
        if s.get("disposition", {}).get("attached_pic"):
            entry["attached_pic"] = True
        streams.append(entry)
    dur = fmt.get("duration")
    return {
        "container": fmt.get("format_name"),
        "duration_seconds": float(dur) if dur not in (None, "N/A") else None,
        "bit_rate": int(fmt["bit_rate"]) if str(fmt.get("bit_rate", "")).isdigit() else None,
        "streams": streams,
    }


def _stream_types(media: dict[str, Any]) -> set[str]:
    return {s.get("codec_type") for s in media.get("streams", []) if not s.get("attached_pic")}


# --------------------------------------------------------------------------- binary formats


def _detect_zip(path: str) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            names = {i.filename for i in infos}
            if infos and infos[0].filename == "mimetype" and infos[0].compress_type == zipfile.ZIP_STORED:
                mt = zf.read("mimetype")[:128].decode("ascii", "ignore").strip()
                odf = {
                    "application/vnd.oasis.opendocument.text": "odt",
                    "application/vnd.oasis.opendocument.spreadsheet": "ods",
                    "application/vnd.oasis.opendocument.presentation": "odp",
                }
                if mt in odf:
                    return odf[mt], "zip-mimetype-entry"
            if "[Content_Types].xml" in names:
                if "word/document.xml" in names:
                    return "docx", "zip-ooxml-part"
                if "xl/workbook.xml" in names:
                    return "xlsx", "zip-ooxml-part"
                if "ppt/presentation.xml" in names:
                    return "pptx", "zip-ooxml-part"
            return "zip", "zip-central-directory"
    except (zipfile.BadZipFile, OSError, NotImplementedError, RuntimeError):
        return "", "zip-damaged"


def _detect_ole(path: str) -> str | None:
    found = _scan_for(path, [_u16("WordDocument"), _u16("Workbook"), _u16("PowerPoint Document")])
    if _u16("WordDocument") in found:
        return "doc"
    if _u16("Workbook") in found:
        return "xls"
    if _u16("PowerPoint Document") in found:
        return "ppt"
    return None


def _detect_gzip(path: str) -> str:
    try:
        with gzip.open(path, "rb") as g:
            head = g.read(512)
    except (OSError, EOFError):
        return "gz"
    return "tgz" if len(head) >= 262 and head[257:262] == b"ustar" else "gz"


def _detect_binary(head: bytes, path: str, workdir: str, det: Detection) -> str | None:  # noqa: C901
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        return "tiff"
    if (
        head[:2] == b"BM"
        and len(head) >= 18
        and int.from_bytes(head[14:18], "little") in (12, 40, 52, 56, 64, 108, 124)
    ):
        return "bmp"
    if head[:4] == b"\x00\x00\x01\x00" and int.from_bytes(head[4:6], "little") > 0:
        return "ico"
    if head[:4] == b"RIFF" and len(head) >= 12:
        return {b"WEBP": "webp", b"WAVE": "wav", b"AVI ": "avi"}.get(head[8:12])
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if head[:5] == b"%PDF-" or b"%PDF-" in head[:1024]:
        return "pdf"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        fmt, how = _detect_zip(path)
        if not fmt:
            det.damaged = True
            det.notes.append("The file looks like a ZIP-based file but its structure is damaged.")
            return None
        det.method = how
        return fmt
    if head[:2] == b"\x1f\x8b":
        return _detect_gzip(path)
    if head[257:262] == b"ustar":
        return "tar"
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    if head[:8] == OLE_MAGIC:
        return _detect_ole(path)
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"\x00\x00\x01\xba":
        return "mpeg"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm" if b"webm" in head[:64] else "mkv"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis"):
            return "avif"
        if brand == b"qt  ":
            return "mov"
        if brand in (b"M4A ", b"M4B ", b"M4P "):
            return "m4a"
        if brand[:3] == b"hei" or brand in (b"mif1", b"msf1"):
            det.notes.append("HEIC/HEIF images are recognised but not supported yet.")
            return None
        return "mp4"
    return _detect_audio_by_probe(head, path, workdir, det)


def _detect_audio_by_probe(head: bytes, path: str, workdir: str, det: Detection) -> str | None:
    """MP3 / ADTS-AAC / Ogg need codec-level disambiguation."""
    is_id3 = head[:3] == b"ID3"
    is_ogg = head[:4] == b"OggS"
    sync = len(head) > 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0
    if not (is_id3 or is_ogg or sync):
        return None
    if sync and not is_id3:
        layer = (head[1] >> 1) & 3
        guess = "aac" if layer == 0 else ("mp3" if layer == 1 else None)
        if guess is None:
            return None
    probe = ffprobe(path, workdir)
    if not probe:
        det.notes.append("Audio container could not be confirmed by ffprobe.")
        return None
    media = _summarize_probe(probe)
    det.details["media"] = media
    fname = probe.get("format", {}).get("format_name") or ""
    codecs = {s.get("codec_name") for s in media["streams"]}
    if is_ogg:
        if "opus" in codecs:
            return "opus"
        if "vorbis" in codecs and "video" not in _stream_types(media):
            return "ogg"
        det.notes.append(f"Unsupported Ogg payload ({', '.join(sorted(c for c in codecs if c))}).")
        return None
    if "mp3" in fname.split(","):
        return "mp3"
    if "aac" in fname.split(","):
        return "aac"
    return None


# --------------------------------------------------------------------------- text formats


def _looks_text(head: bytes) -> str | None:
    if b"\x00" in head:
        # allow UTF-16 with BOM; anything else with NULs is binary
        if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
            try:
                return head.decode("utf-16")
            except UnicodeDecodeError:
                return None
        return None
    for enc in ("utf-8-sig",):
        try:
            return head.decode(enc)
        except UnicodeDecodeError as exc:
            # a multibyte char cut at the end of the window is fine
            if exc.start >= len(head) - 4:
                return head[: exc.start].decode(enc)
    return None


_NAME = re.compile(r"<([A-Za-z_][\w.\-]*:)?([A-Za-z_][\w.\-]*)")


def _xml_root(head: bytes) -> str | None:
    """Local name of the root element, found by skipping prolog items. Never parses entities or DTDs,
    so hostile prologs (entity bombs, XXE) cannot affect detection; real parsing happens in hardened converters."""
    text = head.decode("utf-8", "ignore").lstrip("\ufeff")
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if text.startswith("<?", i):
            j = text.find("?>", i)
        elif text.startswith("<!--", i):
            j = text.find("-->", i)
        elif text.startswith("<!", i):  # DOCTYPE, possibly with an [internal subset]
            depth, j = 0, i
            while j < n:
                c = text[j]
                depth += c == "["
                depth -= c == "]"
                if c == ">" and depth <= 0:
                    break
                j += 1
            j = j if j < n else -1
        else:
            m = _NAME.match(text, i)
            return m.group(2).lower() if m else None
        if j == -1:
            return None
        i = j + (2 if text.startswith("<?", i) else 3 if text.startswith("<!--", i) else 1)
    return None


def _detect_text(head: bytes, path: str, filename: str, size: int, det: Detection) -> str | None:  # noqa: C901
    text = _looks_text(head)
    if text is None:
        return None
    ext_fmt = format_from_extension(filename)
    stripped = text.lstrip()
    low = stripped[:512].lower()

    if low.startswith("<!doctype html") or low.startswith("<html") or "<html" in low[:400]:
        return "html"
    if stripped.startswith("<"):
        root = _xml_root(head)
        if root == "svg":
            return "svg"
        if root == "html":
            return "html"
        if root:
            return "xml"
        det.notes.append("Starts like markup but could not be parsed as XML.")

    if stripped[:1] in "{[" and size <= MAX_TEXT_PARSE_BYTES:
        try:
            with open(path, "rb") as fh:
                json.loads(fh.read().decode("utf-8-sig"))
            return "json"
        except (ValueError, UnicodeDecodeError, RecursionError):
            det.notes.append("Looks like JSON but does not parse; treating as plain text.")
    elif stripped[:1] in "{[":
        det.confidence = "medium"
        det.notes.append("Too large to fully validate as JSON.")
        return "json"

    if ext_fmt in ("yaml", "json") and size <= MAX_TEXT_PARSE_BYTES:
        import yaml

        try:
            with open(path, "rb") as fh:
                doc = yaml.safe_load(fh.read().decode("utf-8-sig"))
            if isinstance(doc, (dict, list)):
                det.confidence = "medium"
                return "yaml"
        except Exception:
            det.notes.append("Has a YAML/JSON extension but does not parse; treating as plain text.")
            return "txt"
    if ext_fmt in ("csv", None, "txt") and _csv_like(text):
        det.confidence = "medium"
        return "csv"
    if ext_fmt == "md":
        det.confidence = "medium"
        return "md"
    det.confidence = "medium"
    return "txt"


def _csv_like(text: str) -> bool:
    lines = [ln for ln in text.splitlines()[:30] if ln.strip()]
    if len(lines) < 2:
        return False
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines), delimiters=",;\t|")
    except csv.Error:
        return False
    counts = {len(r) for r in csv.reader(lines, dialect)}
    return len(counts) == 1 and next(iter(counts)) > 1


# --------------------------------------------------------------------------- details


def _image_details(path: str, fmt: str, det: Detection) -> None:
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(path) as im:
            w, h = im.size
            if w * h > MAX_IMAGE_PIXELS:
                det.rejected_reason = f"Image is {w}x{h} pixels, which exceeds the safe processing limit."
                return
            det.details.update(
                width=w,
                height=h,
                frames=getattr(im, "n_frames", 1),
                has_alpha=("A" in im.getbands()) or ("transparency" in im.info),
                mode=im.mode,
            )
    except Image.DecompressionBombError:
        det.rejected_reason = "The image has too many pixels to process safely."
    except Exception as exc:  # Pillow raises many types on malformed data
        det.damaged = True
        det.notes.append(f"Image could not be decoded: {type(exc).__name__}.")


def _pdf_details(path: str, det: Detection) -> None:
    from pypdf import PdfReader

    try:
        r = PdfReader(path, strict=False)
        det.details["encrypted"] = bool(r.is_encrypted)
        if not r.is_encrypted:
            det.details["pages"] = len(r.pages)
    except Exception as exc:
        det.damaged = True
        det.notes.append(f"PDF structure could not be read: {type(exc).__name__}.")


def _av_details(path: str, fmt: str, workdir: str, det: Detection) -> None:
    if "media" not in det.details:
        probe = ffprobe(path, workdir)
        if not probe:
            det.damaged = True
            det.notes.append("Media streams could not be read; the file may be damaged or truncated.")
            return
        det.details["media"] = _summarize_probe(probe)
    media = det.details["media"]
    types = _stream_types(media)
    if fmt in ("mp4", "mov", "mkv", "webm", "avi", "mpeg") and "video" not in types:
        det.notes.append("Container has no video stream.")
    if fmt == "mp4" and "video" not in types and "audio" in types:
        det.format = "m4a"
        det.notes.append("MP4 container holds audio only; classified as M4A.")


# --------------------------------------------------------------------------- entry point


def inspect_file(path: str, filename: str, workdir: str | None = None) -> Detection:
    size = os.path.getsize(path)
    ext_fmt = format_from_extension(filename)
    det = Detection(format=None, confidence="high", method="magic-bytes", extension_format=ext_fmt)
    if size == 0:
        det.rejected_reason = "The file is empty."
        det.confidence = "low"
        return det
    with tempfile.TemporaryDirectory(prefix="ez-inspect-", dir=workdir) as tmp:
        with open(path, "rb") as fh:
            head = fh.read(HEAD_BYTES)
        fmt = _detect_binary(head, path, tmp, det)
        if fmt is None and not det.damaged:
            det.method = "text-heuristics"
            fmt = _detect_text(head, path, filename, size, det)
        det.format = fmt
        if fmt is None:
            det.confidence = "low"
            det.method = "none"
            return det
        det.mime = FORMATS[fmt].mime
        family = FORMATS[fmt].family.value
        if family == "image" and fmt != "svg":
            _image_details(path, fmt, det)
        elif fmt == "pdf":
            _pdf_details(path, det)
        elif family in ("audio", "video"):
            _av_details(path, fmt, tmp, det)
        fmt = det.format
        # Text formats are often indistinguishable by content, so a txt/md/yaml/csv/json
        # extension on text content is not treated as a mismatch.
        tolerant = det.method == "text-heuristics" and ext_fmt in _TEXT_TIEBREAK
        det.extension_mismatch = bool(ext_fmt and fmt and ext_fmt != fmt and not tolerant)
        if det.extension_mismatch:
            det.notes.append(f"File extension suggests {ext_fmt}, but the content is {fmt}. Content wins.")
        if det.damaged:
            det.confidence = "low"
    return det
