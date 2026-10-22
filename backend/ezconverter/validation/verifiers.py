"""Format-specific output verification.

A zero exit status is never evidence of success. Each target format has its own structural
check (decode, parse, stream probe), layered on a generic existence + signature check, plus
comparisons with the source (dimensions, duration, page/row/member counts) where measurable.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..detection.sniffer import MAX_IMAGE_PIXELS as MAX_PIXELS
from ..detection.sniffer import _summarize_probe, ffprobe, inspect_file
from ..domain.formats import FORMATS, Family
from ..engines.base import Check, InputInfo
from ..sandbox import Limits, run_process, which

TEXT_TARGETS = {"txt", "md", "csv", "json", "yaml", "xml", "html", "rtf"}
OFFICE_ZIP = {"docx", "xlsx", "pptx", "odt", "ods", "odp"}
OLE = {"doc", "xls", "ppt"}


@dataclass
class VerificationReport:
    ok: bool
    checks: list[Check]
    size: int = 0
    sha256: str = ""
    detected_format: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [c.to_dict() for c in self.checks],
            "size": self.size,
            "sha256": self.sha256,
            "detected_format": self.detected_format,
            "details": self.details,
        }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def verify_output(
    *,
    target: str,
    source: InputInfo,
    options: dict[str, Any],
    output: Path,
    work_dir: Path,
    extra: list[Check] | None = None,
) -> VerificationReport:
    checks: list[Check] = []
    report = VerificationReport(ok=False, checks=checks)
    if not output.is_file() or output.stat().st_size == 0:
        checks.append(Check("output_exists_nonempty", False, "No output file was produced, or it is empty."))
        return report
    report.size = output.stat().st_size
    checks.append(Check("output_exists_nonempty", True, f"{report.size} bytes"))
    report.sha256 = sha256_file(output)

    det = inspect_file(str(output), f"out.{FORMATS[target].primary_extension}", str(work_dir))
    report.detected_format = det.format
    if target in TEXT_TARGETS:
        pass  # text formats are validated by parsing, below; sniffing text is ambiguous by design
    else:
        checks.append(
            Check(
                "signature_matches_target",
                det.format == target and not det.damaged,
                f"detected {det.format}" + (" (damaged)" if det.damaged else ""),
            )
        )
    family = FORMATS[target].family
    try:
        if family is Family.IMAGE and target != "svg":
            checks += _check_image(target, source, options, output)
        elif family in (Family.AUDIO, Family.VIDEO):
            checks += _check_av(target, source, options, output, work_dir)
        elif target == "pdf":
            checks += _check_pdf(output)
        elif target in OFFICE_ZIP:
            checks += _check_office_zip(target, output)
        elif target in ("zip", "tar", "tgz"):
            checks += _check_archive(target, output)
        elif target == "7z":
            checks += _check_7z(output)
        elif target == "gz":
            checks += _check_gz(output)
        elif target in TEXT_TARGETS:
            checks += _check_text(target, output)
    except Exception as exc:  # a verifier crash is a failed verification, never a pass
        checks.append(
            Check("verifier_error", False, f"Verification could not complete: {type(exc).__name__}")
        )
    checks += extra or []
    report.ok = all(c.passed for c in checks)
    return report


# --------------------------------------------------------------------------- images


def _expected_dims(source: InputInfo, opts: dict[str, Any], target: str) -> tuple[int, int] | None:
    w, h = source.details.get("width"), source.details.get("height")
    if not w or not h:
        return None
    mw, mh = opts.get("max_width", 0), opts.get("max_height", 0)
    scale = min((mw / w) if mw else 1.0, (mh / h) if mh else 1.0, 1.0)
    if scale < 1.0:
        w, h = max(1, round(w * scale)), max(1, round(h * scale))
    return w, h


def _check_image(target: str, source: InputInfo, opts: dict[str, Any], out: Path) -> list[Check]:
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    checks: list[Check] = []
    with Image.open(out) as im:
        frames = getattr(im, "n_frames", 1)
        size = im.size
        for i in range(min(frames, 5000)):
            im.seek(i)
            im.load()
    checks.append(Check("decodes_fully", True, f"{frames} frame(s), {size[0]}x{size[1]}"))
    exp = (
        _expected_dims(source, opts, target)
        if source.format in FORMATS and FORMATS[source.format].family is Family.IMAGE
        else None
    )
    if exp:
        if target == "ico":
            ok = max(size) <= 256
        else:
            ok = size in (exp, (exp[1], exp[0]))  # EXIF rotation may swap axes
            ok = ok or (abs(size[0] - exp[0]) <= 1 and abs(size[1] - exp[1]) <= 1)
        checks.append(Check("dimensions", ok, f"output {size[0]}x{size[1]}, expected {exp[0]}x{exp[1]}"))
    src_frames = source.details.get("frames", 1)
    if src_frames > 1 and target in ("gif", "webp", "png", "avif"):
        ok = (1 <= frames <= src_frames) if target == "gif" else frames == src_frames
        checks.append(Check("frame_count", ok, f"{frames} frame(s), source has {src_frames}"))
    return checks


# --------------------------------------------------------------------------- audio / video


def _decode_test(path: Path, work_dir: Path) -> Check:
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        return Check("full_decode", False, "ffmpeg unavailable for verification")
    r = run_process(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-protocol_whitelist",
            "file",
            "-i",
            str(path),
            "-f",
            "null",
            "-",
        ],
        workdir=work_dir,
        limits=Limits(wall_seconds=600, cpu_seconds=600, isolate_network=False),
        capture_stdout=False,
        sandboxed=False,
    )
    ok = r.returncode == 0 and not r.stderr_tail.strip()
    return Check("full_decode", ok, "all streams decode without errors" if ok else "decoder reported errors")


def _check_av(target: str, source: InputInfo, opts: dict[str, Any], out: Path, work_dir: Path) -> list[Check]:
    probe = ffprobe(str(out), str(work_dir))
    if not probe:
        return [Check("probe", False, "ffprobe could not read the output")]
    media = _summarize_probe(probe)
    real = [s for s in media["streams"] if not s.get("attached_pic")]
    types = {s["codec_type"] for s in real}
    checks = []
    if FORMATS[target].family is Family.AUDIO:
        checks.append(
            Check(
                "has_audio_no_video", "audio" in types and "video" not in types, f"streams: {sorted(types)}"
            )
        )
        a = next((s for s in real if s["codec_type"] == "audio"), {})
        if opts.get("sample_rate") and target != "opus":
            checks.append(
                Check(
                    "sample_rate",
                    str(a.get("sample_rate")) == str(opts["sample_rate"]),
                    f"{a.get('sample_rate')} Hz",
                )
            )
        if opts.get("channels"):
            checks.append(
                Check("channels", a.get("channels") == opts["channels"], f"{a.get('channels')} channel(s)")
            )
    else:
        checks.append(Check("has_video", "video" in types, f"streams: {sorted(types)}"))
        src_audio = any(
            s.get("codec_type") == "audio" for s in source.details.get("media", {}).get("streams", [])
        )
        if src_audio:
            checks.append(
                Check(
                    "audio_preserved",
                    "audio" in types,
                    "audio track present" if "audio" in types else "audio track missing",
                )
            )
        v = next((s for s in real if s["codec_type"] == "video"), {})
        if opts.get("max_height") and v.get("height"):
            checks.append(
                Check(
                    "height_limit", v["height"] <= opts["max_height"], f"{v.get('width')}x{v.get('height')}"
                )
            )
    sd = source.details.get("media", {}).get("duration_seconds")
    od = media.get("duration_seconds")
    if sd and od is not None:
        tol = max(0.5, 0.05 * sd)
        checks.append(
            Check("duration", abs(od - sd) <= tol, f"output {od:.2f}s vs source {sd:.2f}s (±{tol:.2f}s)")
        )
    checks.append(_decode_test(out, work_dir))
    return checks


# --------------------------------------------------------------------------- documents


def _check_pdf(out: Path) -> list[Check]:
    from pypdf import PdfReader

    r = PdfReader(str(out), strict=False)
    n = len(r.pages)
    return [Check("pdf_parses", n >= 1 and not r.is_encrypted, f"{n} page(s)")]


def _check_office_zip(target: str, out: Path) -> list[Check]:
    from defusedxml import ElementTree as DET

    with zipfile.ZipFile(out) as zf:
        bad = zf.testzip()
        main = {
            "docx": "word/document.xml",
            "xlsx": "xl/workbook.xml",
            "pptx": "ppt/presentation.xml",
            "odt": "content.xml",
            "ods": "content.xml",
            "odp": "content.xml",
        }[target]
        DET.fromstring(zf.read(main))
    return [
        Check(
            "container_integrity", bad is None, "zip CRCs valid" if bad is None else f"corrupt member {bad}"
        ),
        Check("main_part_parses", True, main),
    ]


def _check_archive(target: str, out: Path) -> list[Check]:
    from ..engines.archives import safe_member_name

    n = 0
    if target == "zip":
        with zipfile.ZipFile(out) as zf:
            bad = zf.testzip()
            for zi in zf.infolist():
                safe_member_name(zi.filename)
                n += 1
        return [Check("archive_integrity", bad is None, f"{n} entr{'y' if n == 1 else 'ies'}, CRCs valid")]
    with tarfile.open(out, "r:gz" if target == "tgz" else "r:") as tf:
        for ti in tf:
            safe_member_name(ti.name)
            n += 1
            if ti.isreg():
                f = tf.extractfile(ti)
                while f and f.read(1024 * 1024):
                    pass
    return [Check("archive_integrity", True, f"{n} entr{'y' if n == 1 else 'ies'} readable")]


def _check_7z(out: Path) -> list[Check]:
    import py7zr

    with py7zr.SevenZipFile(out, "r") as sz:
        ok = sz.testzip() is None
        n = len(sz.getnames())
    return [Check("archive_integrity", ok, f"{n} entries")]


def _check_gz(out: Path) -> list[Check]:
    with gzip.open(out, "rb") as g:
        total = 0
        while chunk := g.read(1024 * 1024):
            total += len(chunk)
    return [Check("gzip_integrity", True, f"{total} bytes when decompressed")]


# --------------------------------------------------------------------------- text-like


def _check_text(target: str, out: Path) -> list[Check]:
    raw = out.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [Check("valid_utf8", False, "output is not valid UTF-8")]
    checks = [Check("valid_utf8", True)]
    if target == "json":
        json.loads(text)
        checks.append(Check("json_parses", True))
    elif target == "yaml":
        import yaml

        yaml.safe_load(text)
        checks.append(Check("yaml_parses", True))
    elif target == "xml":
        from defusedxml import ElementTree as DET

        DET.fromstring(raw)
        checks.append(Check("xml_parses", True))
    elif target == "csv":
        rows = list(csv.reader(io.StringIO(text, newline="")))
        widths = {len(r) for r in rows if r}
        checks.append(
            Check(
                "csv_rectangular",
                len(widths) <= 1 and len(rows) >= 1,
                f"{len(rows)} row(s), widths {sorted(widths)}",
            )
        )
    elif target == "html":
        from html.parser import HTMLParser

        class P(HTMLParser):
            tags = 0

            def handle_starttag(self, tag, attrs):  # noqa: ANN001
                self.tags += 1

        p = P()
        p.feed(text)
        checks.append(Check("html_has_markup", p.tags > 0, f"{p.tags} tag(s)"))
    elif target == "rtf":
        ok = text.startswith("{\\rtf") and text.count("{") >= text.count("}") >= 1
        checks.append(Check("rtf_structure", ok))
    elif target == "txt":
        checks.append(
            Check(
                "non_empty_text",
                bool(text.strip()),
                "has text" if text.strip() else "output contains no text",
            )
        )
    return checks
