"""Document conversion: LibreOffice (MPL-2.0), Pandoc (GPL-2.0-or-later), Poppler/pypdf (PDF).

LibreOffice runs headless with a throw-away per-job profile, macros disabled and no network
(the worker runs in an empty network namespace). Pandoc runs with --sandbox.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..domain.capability import Capability, Fidelity, LicenseInfo, Operation, OptionSpec, ResourceHint
from ..domain.errors import ErrorCode, EZError
from ..sandbox import run_process, which
from .base import Assessment, Availability, EngineAdapter, ExecutionContext, ExecutionResult, InputInfo

_LO_LICENSE = LicenseInfo(
    "LibreOffice",
    "MPL-2.0",
    "Used as an external executable; do not modify or link. Bundled fonts have separate licenses.",
)
_PANDOC_LICENSE = LicenseInfo(
    "Pandoc",
    "GPL-2.0-or-later",
    "Invoked as a separate executable only. Redistributing the binary requires providing its source offer.",
)
_POPPLER_LICENSE = LicenseInfo("Poppler utils", "GPL-2.0-or-later", "Invoked as a separate executable only.")
_PYPDF_LICENSE = LicenseInfo("pypdf", "BSD-3-Clause")

TEXT_DOCS = ("docx", "doc", "odt", "rtf", "txt", "html")
SHEETS = ("xlsx", "xls", "ods")
SLIDES = ("pptx", "ppt", "odp")

# LibreOffice export filter per (target). Writer-family sources need distinct pdf filters.
LO_FILTER = {
    "docx": "docx:MS Word 2007 XML",
    "doc": "doc:MS Word 97",
    "odt": "odt:writer8",
    "rtf": "rtf:Rich Text Format",
    "txt": "txt:Text (encoded):UTF8",
    "html": "html:XHTML Writer File:UTF8",
    "xlsx": "xlsx:Calc MS Excel 2007 XML",
    "xls": "xls:MS Excel 97",
    "ods": "ods:calc8",
    "csv": "csv:Text - txt - csv (StarCalc):44,34,76,1",
    "pptx": "pptx:Impress MS PowerPoint 2007 XML",
    "ppt": "ppt:MS PowerPoint 97",
    "odp": "odp:impress8",
    "png": "png:writer_png_Export",
    "pdf": "pdf",
}
PDF_FILTER = {
    **{s: "pdf:writer_pdf_Export" for s in ("docx", "doc", "odt", "rtf", "txt")},
    "html": "pdf:writer_pdf_Export",
    **{s: "pdf:calc_pdf_Export" for s in (*SHEETS, "csv")},
    **{s: "pdf:impress_pdf_Export" for s in SLIDES},
    "svg": "pdf:draw_pdf_Export",
}
IN_FILTER = {
    "html": "HTML (StarWriter)",
    "csv": "CSV:44,34,76,1,,0,false,true,false,false,false,-1",
    "txt": "Text (encoded):UTF8,LF,,,",
}
NO_EXT_SAFE = {"svg": "svg"}

_LO_REG = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="FirstRun" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Load"><prop oor:name="UpdateDocMode" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Inet/Settings"><prop oor:name="ooInetProxyType" oor:op="fuse"><value>2</value></prop></item>
<item oor:path="/org.openoffice.Inet/Settings"><prop oor:name="ooInetHTTPProxyName" oor:op="fuse"><value>127.0.0.1</value></prop></item>
<item oor:path="/org.openoffice.Inet/Settings"><prop oor:name="ooInetHTTPProxyPort" oor:op="fuse"><value>9</value></prop></item>
<item oor:path="/org.openoffice.Inet/Settings"><prop oor:name="ooInetHTTPSProxyName" oor:op="fuse"><value>127.0.0.1</value></prop></item>
<item oor:path="/org.openoffice.Inet/Settings"><prop oor:name="ooInetHTTPSProxyPort" oor:op="fuse"><value>9</value></prop></item>
<item oor:path="/org.openoffice.Inet/Settings"><prop oor:name="ooInetFTPProxyName" oor:op="fuse"><value>127.0.0.1</value></prop></item>
<item oor:path="/org.openoffice.Inet/Settings"><prop oor:name="ooInetFTPProxyPort" oor:op="fuse"><value>9</value></prop></item>
<item oor:path="/org.openoffice.Inet/Settings"><prop oor:name="ooInetNoProxy" oor:op="fuse"><value></value></prop></item>
</oor:items>
"""
# Defence in depth for hosts without network namespaces (Windows, default Docker): every outbound HTTP/HTTPS/FTP
# request LibreOffice makes while importing a document (linked images, stylesheets, OLE links) is sent to a proxy on
# a closed port, so it fails instead of reaching the internet or the local network. NoProxy is emptied on purpose.
_NO_NETWORK_ENV = {
    k: "http://127.0.0.1:9" for k in ("http_proxy", "https_proxy", "ftp_proxy", "HTTP_PROXY", "HTTPS_PROXY", "FTP_PROXY")
} | {"no_proxy": "", "NO_PROXY": ""}


def _pairs() -> list[tuple[str, str, str]]:
    """(source, target, kind) for LibreOffice; kind: 'doc' | 'pdf' | 'raster'."""
    out: list[tuple[str, str, str]] = []
    for group, extra in ((TEXT_DOCS, ("pdf",)), (SHEETS, ("csv", "pdf")), (SLIDES, ("pdf",))):
        for s in group:
            for t in (*group, *extra):
                if s == t:
                    continue
                out.append((s, t, "pdf" if t == "pdf" else "doc"))
    for t in (*SHEETS, "pdf"):
        out.append(("csv", t, "pdf" if t == "pdf" else "doc"))
    out += [("svg", "png", "raster"), ("svg", "pdf", "pdf")]
    return out


def _user_installation_arg(profile: Path) -> str:
    """The -env:UserInstallation argument; a proper file URI (file:///C:/... on Windows, file:///... on POSIX)."""
    return f"-env:UserInstallation={profile.absolute().as_uri()}"


@lru_cache(maxsize=1)
def _lo_version() -> str | None:
    exe = which("soffice")
    if not exe:
        return None
    try:
        return (
            subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
            or None
        )
    except (OSError, subprocess.SubprocessError):
        return None


class LibreOfficeEngine(EngineAdapter):
    engine_id = "libreoffice"

    def capabilities(self) -> list[Capability]:
        caps = []
        for s, t, kind in _pairs():
            if kind == "raster":
                fid, op, warn = (
                    Fidelity.LAYOUT_ALTERING,
                    Operation.RASTERIZE,
                    ("Vector content is rasterised at a fixed size.",),
                )
            elif t == "pdf":
                fid, op = Fidelity.LAYOUT_ALTERING, Operation.CONVERT
                warn = ("Pagination and fonts depend on the fonts installed on the converting machine.",)
            elif t in ("txt", "csv"):
                fid, op = Fidelity.SEMANTIC, Operation.CONVERT
                warn = ("Formatting, images and styles are discarded; only text content is kept.",) + (
                    ("Only the first sheet is exported.",) if t == "csv" else ()
                )
            elif s in SLIDES or s in SHEETS or s in TEXT_DOCS or s == "csv":
                fid, op = Fidelity.LAYOUT_ALTERING, Operation.CONVERT
                warn = (
                    "Features unsupported by the target format may be altered or dropped; layout can shift slightly.",
                )
            caps.append(
                Capability(
                    id=f"libreoffice.{s}->{t}",
                    engine_id=self.engine_id,
                    source=s,
                    target=t,
                    operation=op,
                    fidelity=fid,
                    summary=f"Convert {s.upper()} to {t.upper()} with LibreOffice",
                    options=(),
                    requires=("exe:soffice",),
                    warnings=warn,
                    license=_LO_LICENSE,
                    resources=ResourceHint(
                        cpu_heavy=True,
                        memory_factor=20,
                        memory_floor_mb=768,
                        seconds_per_mb=2.0,
                        max_input_mb=200,
                    ),
                    composable=(t in ("pdf", "docx") or (s == "svg" and t == "png")) and s != "csv",
                )
            )
        return caps

    def availability(self, cap: Capability) -> Availability:
        if not which("soffice"):
            return Availability(False, "LibreOffice (soffice) is not installed.")
        return Availability(True, version=_lo_version())

    def assess(self, cap: Capability, info: InputInfo, options: dict[str, Any]) -> Assessment:
        notes = list(cap.warnings)
        if info.format in ("docx", "xlsx", "pptx", "doc", "xls", "ppt"):
            notes.append("Macros and active content are not executed or carried over.")
        return Assessment(cap.fidelity, notes)

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        cap, info = ctx.capability, ctx.input
        s, t = cap.source, cap.target
        soffice = which("soffice")
        assert soffice
        work = ctx.work_dir
        profile = work / "lo-profile"
        (profile / "user").mkdir(parents=True)
        (profile / "user" / "registrymodifications.xcu").write_text(_LO_REG)
        indir, outdir = work / "in", work / "lo-out"
        indir.mkdir()
        outdir.mkdir()
        src = indir / f"input.{s}"
        shutil.copyfile(info.path, src)  # fixed safe name; extension steers LibreOffice's import filter

        export = PDF_FILTER[s] if t == "pdf" else LO_FILTER[t]
        argv = [
            soffice,
            _user_installation_arg(profile),
            "--headless",
            "--norestore",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--nofirststartwizard",
        ]
        if s in IN_FILTER:
            argv.append(f"--infilter={IN_FILTER[s]}")
        argv += ["--convert-to", export, "--outdir", str(outdir), str(src)]
        ctx.progress(None, "converting")
        r = run_process(argv, workdir=work, limits=ctx.limits, cancel=ctx.cancel, sandboxed=False, env_extra=_NO_NETWORK_ENV)
        if r.cancelled:
            raise EZError(ErrorCode.CANCELLED, "The conversion was cancelled.")
        if r.timed_out:
            raise EZError(ErrorCode.TIMEOUT, "LibreOffice took too long and was stopped.")
        if r.killed_by_signal:
            raise EZError(ErrorCode.RESOURCE_LIMIT, "The conversion exceeded its resource limits.")
        produced = [p for p in outdir.iterdir() if p.is_file()]
        if r.returncode != 0 or not produced:
            raise EZError(
                ErrorCode.CONVERSION_FAILED,
                "LibreOffice could not convert this file; it may be damaged or use unsupported features.",
            )
        ext = "txt" if t == "txt" else t
        out = ctx.output_path(ext)
        shutil.move(str(produced[0]), out)
        return ExecutionResult(out, [], {"libreoffice": _lo_version() or "unknown"})


# ---------------------------------------------------------------------------------- pandoc

_STRIP_FILTER = Path(__file__).with_name("data") / "strip_external.lua"
PANDOC_PAIRS = [
    ("md", "html"),
    ("md", "docx"),
    ("md", "odt"),
    ("md", "rtf"),
    ("md", "txt"),
    ("html", "md"),
    ("docx", "md"),
    ("odt", "md"),
    ("txt", "md"),
    ("rtf", "md"),
]
_PD_FMT = {"md": "gfm", "html": "html", "docx": "docx", "odt": "odt", "rtf": "rtf", "txt": "plain"}
_PD_IN = {"md": "gfm", "html": "html", "docx": "docx", "odt": "odt", "rtf": "rtf", "txt": "markdown"}


@lru_cache(maxsize=1)
def _pandoc_version() -> str | None:
    exe = which("pandoc")
    if not exe:
        return None
    out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20).stdout
    return out.splitlines()[0] if out else None


class PandocEngine(EngineAdapter):
    engine_id = "pandoc"

    def capabilities(self) -> list[Capability]:
        caps = []
        for s, t in PANDOC_PAIRS:
            warn = ["Markdown has no page layout: styles are mapped to the target's defaults."]
            if s != "md":
                warn = [
                    "Styling, images and complex tables are simplified; only the document structure and text are kept.",
                    "Images and raw content are not carried over (disabled for safety).",
                ]
            if t == "txt":
                warn = ["All formatting is removed; only text is kept."]
            caps.append(
                Capability(
                    id=f"pandoc.{s}->{t}",
                    engine_id=self.engine_id,
                    source=s,
                    target=t,
                    operation=Operation.CONVERT,
                    fidelity=Fidelity.SEMANTIC if t == "txt" or s != "md" else Fidelity.LAYOUT_ALTERING,
                    summary=f"Convert {s.upper()} to {t.upper()} with Pandoc",
                    options=(),
                    requires=("exe:pandoc",),
                    warnings=tuple(warn),
                    license=_PANDOC_LICENSE,
                    resources=ResourceHint(memory_factor=10, seconds_per_mb=1.0, max_input_mb=50),
                    composable=(s == "md" and t in ("docx", "html")),
                )
            )
        return caps

    def availability(self, cap: Capability) -> Availability:
        if not which("pandoc"):
            return Availability(False, "pandoc is not installed.")
        return Availability(True, version=_pandoc_version())

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        cap = ctx.capability
        s, t = cap.source, cap.target
        pandoc = which("pandoc")
        assert pandoc
        out = ctx.output_path(t if t != "md" else "md")
        # --sandbox blocks all filesystem IO from readers/writers, but docx/odt writers must read their bundled
        # reference templates from disk on distro builds. For those writers external resources are neutralised
        # by the filter below (images -> alt text, raw content removed) so a crafted ![](/path) cannot embed files.
        argv = [pandoc, "-f", _PD_IN[s], "-t", _PD_FMT[t], "-o", str(out), "--lua-filter", str(_STRIP_FILTER)]
        if t not in ("docx", "odt"):
            argv.insert(1, "--sandbox")
        if t == "html":
            argv += ["--standalone", "--metadata", "title=Document"]
        elif t == "rtf":
            argv += ["--standalone"]  # without it Pandoc emits a bare fragment lacking the {\\rtf1 header
        if t == "plain":
            argv += ["--wrap=none"]
        if t == "md" or _PD_FMT[t] == "gfm":
            argv += ["--wrap=none"]
        argv.append(str(ctx.input.path))
        ctx.progress(None, "converting")
        r = run_process(argv, workdir=ctx.work_dir, limits=ctx.limits, cancel=ctx.cancel, sandboxed=False)
        if r.cancelled:
            raise EZError(ErrorCode.CANCELLED, "The conversion was cancelled.")
        if r.timed_out:
            raise EZError(ErrorCode.TIMEOUT, "Pandoc took too long and was stopped.")
        if r.returncode != 0 or not out.exists():
            raise EZError(
                ErrorCode.CONVERSION_FAILED,
                "Pandoc could not convert this file.",
                {"engine_message": re.sub(r"/[^\s:]+", "<path>", r.stderr_tail)[-400:]},
            )
        return ExecutionResult(out, [], {"pandoc": _pandoc_version() or "unknown"})


# ---------------------------------------------------------------------------------- pdf

PDF_IMAGE_TARGETS = {"png": "-png", "jpeg": "-jpeg", "tiff": "-tiff"}


class PdfEngine(EngineAdapter):
    """PDF text extraction (pypdf) and page rasterisation (pdftoppm)."""

    engine_id = "pdf"

    def capabilities(self) -> list[Capability]:
        caps = [
            Capability(
                id="pypdf.pdf->txt",
                engine_id=self.engine_id,
                source="pdf",
                target="txt",
                operation=Operation.CONVERT,
                fidelity=Fidelity.SEMANTIC,
                summary="Extract the text layer of a PDF",
                requires=("py:pypdf",),
                warnings=(
                    "Only embedded text is extracted (no OCR); layout, images and reading order may not be preserved.",
                ),
                license=_PYPDF_LICENSE,
                resources=ResourceHint(memory_factor=8, max_input_mb=200),
            )
        ]
        for t in PDF_IMAGE_TARGETS:
            caps.append(
                Capability(
                    id=f"poppler.pdf->{t}",
                    engine_id=self.engine_id,
                    source="pdf",
                    target=t,
                    operation=Operation.RASTERIZE,
                    fidelity=Fidelity.LAYOUT_ALTERING,
                    summary=f"Render one PDF page as {t.upper()}",
                    options=(
                        OptionSpec("page", "int", 1, "Page number to render (1-based).", 1, 100000),
                        OptionSpec("dpi", "int", 150, "Render resolution.", 36, 600),
                    ),
                    requires=("exe:pdftoppm",),
                    warnings=("Only the selected page is rendered; text becomes pixels.",),
                    license=_POPPLER_LICENSE,
                    resources=ResourceHint(memory_factor=6, max_input_mb=200),
                    composable=(t == "png"),
                )
            )
        return caps

    def availability(self, cap: Capability) -> Availability:
        if cap.id.startswith("poppler.") and not which("pdftoppm"):
            return Availability(False, "poppler-utils (pdftoppm) is not installed.")
        return Availability(True, version="pypdf" if cap.id.startswith("pypdf") else None)

    def assess(self, cap: Capability, info: InputInfo, options: dict[str, Any]) -> Assessment:
        d = info.details
        if d.get("encrypted"):
            raise EZError(
                ErrorCode.UNSUPPORTED_INPUT,
                "This PDF is password-protected. Remove the password and try again.",
            )
        notes = list(cap.warnings)
        if cap.id.startswith("poppler."):
            pages = d.get("pages")
            if pages and options["page"] > pages:
                raise EZError(
                    ErrorCode.INVALID_OPTIONS,
                    f"Option 'page': the document has only {pages} page(s).",
                    {"option": "page", "pages": pages},
                )
            if pages and pages > 1:
                notes.append(f"The document has {pages} pages; only page {options['page']} is rendered.")
        return Assessment(cap.fidelity, notes)

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        if ctx.capability.target == "txt":
            return self._extract_text(ctx)
        return self._rasterize(ctx)

    def _extract_text(self, ctx: ExecutionContext) -> ExecutionResult:
        from pypdf import PdfReader

        out = ctx.output_path("txt")
        try:
            reader = PdfReader(str(ctx.input.path), strict=False)
            parts = []
            for i, page in enumerate(reader.pages):
                if ctx.cancel.is_set():
                    raise EZError(ErrorCode.CANCELLED, "The conversion was cancelled.")
                parts.append(page.extract_text() or "")
                ctx.progress((i + 1) / max(1, len(reader.pages)), "extracting")
        except EZError:
            raise
        except Exception as exc:
            raise EZError(ErrorCode.MALFORMED_INPUT, "The PDF could not be read; it may be damaged.") from exc
        text = "\n\f\n".join(parts).strip()
        if not text:
            raise EZError(
                ErrorCode.CONVERSION_FAILED,
                "This PDF has no extractable text (it may be a scanned image). OCR is not supported.",
            )
        out.write_text(text + "\n", encoding="utf-8")
        return ExecutionResult(out, [], {"pypdf": __import__("pypdf").__version__})

    def _rasterize(self, ctx: ExecutionContext) -> ExecutionResult:
        t = ctx.capability.target
        exe = which("pdftoppm")
        assert exe
        o = ctx.options
        # Refuse pages whose rendered size would be unreasonable.
        pdfinfo = which("pdfinfo")
        if pdfinfo:
            r = run_process(
                [pdfinfo, "-f", str(o["page"]), "-l", str(o["page"]), str(ctx.input.path)],
                workdir=ctx.work_dir,
                limits=ctx.limits,
                sandboxed=False,
            )
            m = re.search(r"Page\s+\d+\s+size:\s+([\d.]+)\s+x\s+([\d.]+)", r.stdout)
            if m:
                px = (float(m.group(1)) / 72 * o["dpi"]) * (float(m.group(2)) / 72 * o["dpi"])
                if px > 100_000_000:
                    raise EZError(
                        ErrorCode.UNSAFE_INPUT,
                        "The requested page would render to too many pixels. Lower the dpi.",
                    )
        stem = ctx.work_dir / "page"
        argv = [
            exe,
            PDF_IMAGE_TARGETS[t],
            "-r",
            str(o["dpi"]),
            "-f",
            str(o["page"]),
            "-l",
            str(o["page"]),
            "-singlefile",
            str(ctx.input.path),
            str(stem),
        ]
        ctx.progress(None, "rendering")
        r = run_process(argv, workdir=ctx.work_dir, limits=ctx.limits, cancel=ctx.cancel, sandboxed=False)
        if r.cancelled:
            raise EZError(ErrorCode.CANCELLED, "The conversion was cancelled.")
        if r.timed_out:
            raise EZError(ErrorCode.TIMEOUT, "Rendering took too long and was stopped.")
        ext = {"png": "png", "jpeg": "jpg", "tiff": "tif"}[t]
        produced = ctx.work_dir / f"page.{ext}"
        if r.returncode != 0 or not produced.exists():
            raise EZError(ErrorCode.CONVERSION_FAILED, "The PDF page could not be rendered.")
        out = ctx.output_path(ext)
        shutil.move(str(produced), out)
        return ExecutionResult(out, [], {"poppler": "pdftoppm"})
