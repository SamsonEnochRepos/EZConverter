"""Raster image conversion with Pillow (HPND license).

SVG and vector tracing are intentionally absent: raster -> vector would be an approximation and
is not offered. SVG rasterisation lives in the documents engine (LibreOffice).
"""

from __future__ import annotations

import warnings as pywarnings
from typing import Any

from PIL import Image, ImageOps, features

from ..domain.capability import Capability, Fidelity, LicenseInfo, Operation, OptionSpec, ResourceHint
from ..domain.errors import ErrorCode, EZError
from .base import (
    Assessment,
    Availability,
    EngineAdapter,
    ExecutionContext,
    ExecutionResult,
    InputInfo,
    require_not_cancelled,
)

FORMATS = ("png", "jpeg", "webp", "avif", "gif", "tiff", "bmp", "ico")
PIL_NAME = {
    "png": "PNG",
    "jpeg": "JPEG",
    "webp": "WEBP",
    "avif": "AVIF",
    "gif": "GIF",
    "tiff": "TIFF",
    "bmp": "BMP",
    "ico": "ICO",
}
EXT = {"jpeg": "jpg", "tiff": "tif"}
HAS_ALPHA_FORMATS = {"png", "webp", "avif", "gif", "tiff", "ico"}
ANIMATED_FORMATS = {"gif", "webp", "png", "avif"}  # APNG via PNG plugin
LOSSY_TARGETS = {"jpeg", "gif", "ico"}
QUALITY_TARGETS = {"jpeg", "webp", "avif"}
MAX_PIXELS = 89_000_000
_EXIF_ORIENTATION = 0x0112
_LICENSE = LicenseInfo(
    "Pillow", "HPND", "Permissive; bundled codec libraries carry their own permissive licenses."
)


def _base_options(target: str) -> tuple[OptionSpec, ...]:
    opts = [
        OptionSpec(
            "max_width",
            "int",
            0,
            "Downscale to at most this width in pixels (0 = keep). Never upscales.",
            0,
            16384,
        ),
        OptionSpec(
            "max_height",
            "int",
            0,
            "Downscale to at most this height in pixels (0 = keep). Never upscales.",
            0,
            16384,
        ),
        OptionSpec(
            "background",
            "string",
            "#ffffff",
            "Colour used where transparency must be flattened (hex, e.g. #ffffff).",
        ),
        OptionSpec(
            "keep_metadata", "bool", True, "Keep EXIF and ICC colour profile where the target supports them."
        ),
    ]
    if target in QUALITY_TARGETS:
        opts.append(
            OptionSpec("quality", "int", 85, "Encoder quality (higher is better and larger).", 1, 100)
        )
    if target == "webp":
        opts.append(OptionSpec("lossless", "bool", False, "Use lossless WebP (ignores quality)."))
    return tuple(opts)


class PillowEngine(EngineAdapter):
    engine_id = "pillow"

    def capabilities(self) -> list[Capability]:
        caps = []
        for s in FORMATS:
            for t in FORMATS:
                same = s == t
                fidelity = Fidelity.LOSSY if t in LOSSY_TARGETS else Fidelity.LOSSLESS_REENCODE
                if t == "webp" or t == "avif":
                    fidelity = Fidelity.LOSSY  # default settings are lossy; lossless webp is an option
                caps.append(
                    Capability(
                        id=f"pillow.{s}->{t}",
                        engine_id=self.engine_id,
                        source=s,
                        target=t,
                        operation=Operation.TRANSFORM if same else Operation.CONVERT,
                        fidelity=Fidelity.LOSSY if same and t != "png" else fidelity,
                        summary=(
                            f"Resize/compress {s.upper()}"
                            if same
                            else f"Re-encode {s.upper()} as {t.upper()}"
                        ),
                        options=_base_options(t),
                        requires=("py:PIL",),
                        license=_LICENSE,
                        resources=ResourceHint(memory_factor=12, seconds_per_mb=0.3, max_input_mb=256),
                        composable=(s == "png" and t in ("jpeg", "webp")),
                    )
                )
        return caps

    def availability(self, cap: Capability) -> Availability:
        import PIL

        for fmt in (cap.source, cap.target):
            if fmt == "avif" and not features.check("avif"):
                return Availability(False, "Pillow was built without AVIF support.")
            if fmt == "webp" and not features.check("webp"):
                return Availability(False, "Pillow was built without WebP support.")
        return Availability(True, version=f"Pillow {PIL.__version__}")

    def validate_options(self, cap: Capability, options: dict[str, Any] | None) -> dict[str, Any]:
        opts = super().validate_options(cap, options)
        _parse_colour(opts["background"])
        return opts

    # ------------------------------------------------------------------ assessment

    def assess(self, cap: Capability, info: InputInfo, options: dict[str, Any]) -> Assessment:
        d, t = info.details, cap.target
        notes = list(cap.warnings)
        fidelity = cap.fidelity
        if d.get("has_alpha") and t not in HAS_ALPHA_FORMATS:
            notes.append(
                f"{t.upper()} has no transparency: transparent areas are flattened onto "
                f"{options.get('background', '#ffffff')}."
            )
            fidelity = Fidelity.LOSSY
        if t == "gif" and d.get("has_alpha"):
            notes.append(
                "GIF supports only 1-bit transparency; partially transparent pixels are approximated."
            )
        if t == "gif":
            notes.append("GIF is limited to 256 colours per frame; colours are quantised.")
        if t == "ico":
            notes.append("ICO stores a single image of at most 256x256 pixels; larger images are downscaled.")
        if d.get("frames", 1) > 1 and t not in ANIMATED_FORMATS:
            notes.append(f"{t.upper()} cannot hold animation: only the first frame is kept.")
            fidelity = Fidelity.LOSSY
        if t == "jpeg" and cap.source != "jpeg":
            notes.append("JPEG compression is lossy; repeated conversion degrades quality.")
        if cap.source in ("jpeg",) and t in ("webp", "avif", "gif"):
            notes.append("The source is already lossy; re-encoding adds a second generation of loss.")
        if t == "webp" and options.get("lossless") and fidelity is Fidelity.LOSSY and not notes:
            fidelity = Fidelity.LOSSLESS_REENCODE
        if t == "webp" and options.get("lossless") and not d.get("has_alpha") and d.get("frames", 1) == 1:
            fidelity = Fidelity.LOSSLESS_REENCODE
        if t == "avif":
            notes.append("AVIF encoding can be slow for large images.")
        if options.get("max_width") or options.get("max_height"):
            fidelity = Fidelity.LOSSY
            notes.append("The image is resized, so pixel data is not preserved.")
        pixels = (d.get("width") or 0) * (d.get("height") or 0)
        if pixels > MAX_PIXELS:
            raise EZError(ErrorCode.UNSAFE_INPUT, "The image has too many pixels to process safely.")
        return Assessment(fidelity, _dedupe(notes))

    # ------------------------------------------------------------------ execution

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        import PIL

        t = ctx.capability.target
        opts = ctx.options
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        out = ctx.output_path(EXT.get(t, t))
        warns: list[str] = []
        ctx.progress(None, "decoding")
        try:
            with pywarnings.catch_warnings():
                pywarnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(ctx.input.path) as im:
                    frames = self._load_frames(im, t, opts, warns)
                    icc = im.info.get("icc_profile") if opts["keep_metadata"] else None
                    # Frame 0 of a single-frame image was physically rotated by exif_transpose, so the
                    # copied EXIF must not ask viewers to rotate it a second time.
                    exif = (
                        self._exif(im, transposed=getattr(im, "n_frames", 1) == 1)
                        if opts["keep_metadata"] and t in ("jpeg", "webp", "png", "tiff", "avif")
                        else None
                    )
                    durations = [fr.info.get("_dur", 100) for fr in frames] if len(frames) > 1 else None
                    loop = im.info.get("loop", 0)
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise EZError(ErrorCode.UNSAFE_INPUT, "The image is too large to process safely.") from exc
        except (OSError, SyntaxError, ValueError, EOFError) as exc:
            raise EZError(
                ErrorCode.MALFORMED_INPUT, "The image could not be decoded; it may be damaged."
            ) from exc

        require_not_cancelled(ctx)
        ctx.progress(None, "encoding")
        kwargs: dict[str, Any] = {}
        if t in QUALITY_TARGETS:
            kwargs["quality"] = opts["quality"]
        if t == "webp":
            kwargs["lossless"] = bool(opts.get("lossless"))
            kwargs["method"] = 4
        if t == "jpeg":
            kwargs.update(optimize=True, subsampling="4:2:0" if opts["quality"] < 90 else "4:4:4")
        if t == "png":
            kwargs["optimize"] = True
        if icc:
            kwargs["icc_profile"] = icc
        if exif:
            kwargs["exif"] = exif
        try:
            if len(frames) > 1 and t in ANIMATED_FORMATS:
                first, rest = frames[0], frames[1:]
                kwargs.update(save_all=True, append_images=rest, duration=durations, loop=loop)
                if t == "gif":
                    kwargs["disposal"] = 2
                first.save(out, PIL_NAME[t], **kwargs)
            else:
                frames[0].save(out, PIL_NAME[t], **kwargs)
        except (OSError, ValueError, KeyError) as exc:
            raise EZError(ErrorCode.CONVERSION_FAILED, f"Encoding to {t.upper()} failed.") from exc
        return ExecutionResult(
            out,
            warns,
            {"pillow": PIL.__version__},
            {"frames_written": len(frames) if t in ANIMATED_FORMATS else 1},
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _exif(im: Image.Image, transposed: bool = False) -> bytes | None:
        try:
            ex = im.getexif()
            if transposed and ex.get(_EXIF_ORIENTATION, 1) != 1:
                ex[_EXIF_ORIENTATION] = 1
            return ex.tobytes() if ex else None
        except Exception:
            return None

    def _load_frames(
        self, im: Image.Image, target: str, opts: dict[str, Any], warns: list[str]
    ) -> list[Image.Image]:
        n = getattr(im, "n_frames", 1)
        keep_anim = n > 1 and target in ANIMATED_FORMATS
        indices = range(n) if keep_anim else range(1)
        frames: list[Image.Image] = []
        for i in indices:
            im.seek(i)
            dur = im.info.get("duration", 100)
            fr = ImageOps.exif_transpose(im) if i == 0 and n == 1 else im.copy()
            fr = self._normalise(fr, target, opts)
            fr = self._resize(fr, opts)
            fr.info["_dur"] = dur
            frames.append(fr)
        if n > 1 and not keep_anim:
            warns.append(f"Only the first of {n} frames was converted.")
        return frames

    @staticmethod
    def _normalise(im: Image.Image, target: str, opts: dict[str, Any]) -> Image.Image:
        has_alpha = "A" in im.getbands() or "transparency" in im.info
        if target in ("jpeg", "bmp"):
            if has_alpha:
                rgba = im.convert("RGBA")
                bg = Image.new("RGBA", rgba.size, _parse_colour(opts["background"]) + (255,))
                im = Image.alpha_composite(bg, rgba).convert("RGB")
            elif im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            if target == "jpeg" and im.mode == "P":
                im = im.convert("RGB")
            return im
        if target == "gif":
            return im.convert("RGBA") if has_alpha else im.convert("RGB")
        if target in ("png", "webp", "avif", "ico", "tiff"):
            if (
                im.mode in ("CMYK", "YCbCr", "LAB", "HSV", "I;16", "F")
                and target != "tiff"
                or im.mode == "P"
                or im.mode not in ("1", "L", "LA", "RGB", "RGBA", "I;16", "CMYK")
            ):
                im = im.convert("RGBA" if has_alpha else "RGB")
            return im
        return im

    @staticmethod
    def _resize(im: Image.Image, opts: dict[str, Any]) -> Image.Image:
        mw, mh = opts.get("max_width", 0), opts.get("max_height", 0)
        w, h = im.size
        scale = min((mw / w) if mw else 1.0, (mh / h) if mh else 1.0, 1.0)
        if scale < 1.0:
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
        return im


def _parse_colour(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    try:
        if len(v) != 6:
            raise ValueError
        return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    except ValueError as exc:
        raise EZError(
            ErrorCode.INVALID_OPTIONS,
            "Option 'background': use a hex colour like #ffffff.",
            {"option": "background"},
        ) from exc


def _dedupe(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for i in items:
        seen.setdefault(i, None)
    return list(seen)
