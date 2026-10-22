"""Audio and video conversion with FFmpeg (LGPL/GPL depending on the build).

Arguments are built from validated option values only; user input never reaches a shell, and
input demuxers are pinned to the detected format with a file-only protocol whitelist.
"""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from typing import Any

from ..domain.capability import Capability, Fidelity, LicenseInfo, Operation, OptionSpec, ResourceHint
from ..domain.errors import ErrorCode, EZError
from ..sandbox import run_process, which
from .base import Assessment, Availability, EngineAdapter, ExecutionContext, ExecutionResult, InputInfo

AUDIO = ("mp3", "wav", "flac", "aac", "m4a", "ogg", "opus", "aiff")
VIDEO = ("mp4", "mkv", "mov", "avi", "webm", "mpeg")
LOSSLESS_AUDIO = {"wav", "flac", "aiff"}
INPUT_DEMUXER = {
    "mp3": "mp3",
    "wav": "wav",
    "flac": "flac",
    "aac": "aac",
    "m4a": "mov",
    "ogg": "ogg",
    "opus": "ogg",
    "aiff": "aiff",
    "mp4": "mov",
    "mov": "mov",
    "mkv": "matroska",
    "webm": "matroska",
    "avi": "avi",
    "mpeg": "mpeg",
}
AUDIO_ENCODER = {
    "mp3": "libmp3lame",
    "wav": "pcm_s16le",
    "flac": "flac",
    "aac": "aac",
    "m4a": "aac",
    "ogg": "libvorbis",
    "opus": "libopus",
    "aiff": "pcm_s16be",
}
OUT_MUXER = {
    "mp3": "mp3",
    "wav": "wav",
    "flac": "flac",
    "aac": "adts",
    "m4a": "ipod",
    "ogg": "ogg",
    "opus": "opus",
    "aiff": "aiff",
    "mp4": "mp4",
    "mkv": "matroska",
    "mov": "mov",
    "avi": "avi",
    "webm": "webm",
    "mpeg": "mpeg",
}
OUT_EXT = {"mkv": "mkv", "mpeg": "mpg"}
LOSSY_AUDIO_TARGET = {"mp3", "aac", "m4a", "ogg", "opus"}
DEFAULT_BITRATE = {"mp3": 192, "aac": 160, "m4a": 160, "ogg": 160, "opus": 128}
# (video encoder, audio encoder, default crf)
VIDEO_CODECS = {
    "mp4": ("libx264", "aac", 23),
    "mkv": ("libx264", "aac", 23),
    "mov": ("libx264", "aac", 23),
    "avi": ("mpeg4", "libmp3lame", None),
    "webm": ("libvpx-vp9", "libopus", 32),
    "mpeg": ("mpeg2video", "mp2", None),
}
# Streams that can be copied into a container without re-encoding.
COPY_OK = {
    "mp4": ({"h264", "hevc", "av1", "mpeg4"}, {"aac", "mp3", "ac3", "opus"}),
    "mov": ({"h264", "hevc", "mpeg4"}, {"aac", "mp3", "ac3", "pcm_s16le"}),
    "mkv": (
        {"h264", "hevc", "vp8", "vp9", "av1", "mpeg4", "mpeg2video"},
        {"aac", "mp3", "ac3", "opus", "vorbis", "flac", "pcm_s16le", "mp2"},
    ),
    "webm": ({"vp8", "vp9", "av1"}, {"opus", "vorbis"}),
    "avi": ({"mpeg4", "h264", "mpeg2video"}, {"mp3", "ac3", "pcm_s16le", "mp2"}),
    "mpeg": ({"mpeg2video"}, {"mp2", "mp3", "ac3"}),
}
BITMAP_SUBS = {"dvd_subtitle", "hdmv_pgs_subtitle", "dvb_subtitle", "xsub", "dvb_teletext"}
_LICENSE = LicenseInfo(
    "FFmpeg",
    "LGPL-2.1-or-later / GPL-2.0-or-later (build dependent)",
    "Distribution of GPL builds (libx264/libx265) imposes copyleft obligations; "
    "review before bundling in packaged apps.",
)


@lru_cache(maxsize=1)
def _encoders() -> set[str]:
    exe = which("ffmpeg")
    if not exe:
        return set()
    out = subprocess.run(
        [exe, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20
    ).stdout
    return {m.group(1) for line in out.splitlines() if (m := re.match(r"^\s[VAS][\w.]{5}\s+(\S+)", line))}


@lru_cache(maxsize=1)
def _version() -> str | None:
    exe = which("ffmpeg")
    if not exe:
        return None
    out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=20).stdout
    return out.splitlines()[0] if out else None


def _audio_options(t: str) -> tuple[OptionSpec, ...]:
    opts = [
        OptionSpec("sample_rate", "int", 0, "Resample to this rate in Hz (0 = keep).", 0, 192000),
        OptionSpec("channels", "int", 0, "Channel count (0 = keep).", 0, 8),
    ]
    if t in LOSSY_AUDIO_TARGET:
        opts.append(
            OptionSpec("bitrate_kbps", "int", DEFAULT_BITRATE[t], "Target bitrate in kbit/s.", 32, 320)
        )
    if t in ("wav", "aiff", "flac"):
        opts.append(OptionSpec("bit_depth", "enum", 16, "Sample bit depth.", choices=(16, 24)))
    return tuple(opts)


def _video_options(t: str) -> tuple[OptionSpec, ...]:
    _v, _a, crf = VIDEO_CODECS[t]
    opts = [
        OptionSpec(
            "max_height",
            "int",
            0,
            "Downscale to at most this height in pixels (0 = keep). Never upscales.",
            0,
            4320,
        ),
        OptionSpec("fps", "int", 0, "Output frame rate (0 = keep).", 0, 120),
        OptionSpec(
            "audio_bitrate_kbps", "int", 160, "Audio bitrate in kbit/s when audio is re-encoded.", 32, 320
        ),
        OptionSpec(
            "subtitles",
            "enum",
            "keep",
            "Keep text subtitle tracks where the container supports them.",
            choices=("keep", "drop"),
        ),
        OptionSpec(
            "mode",
            "enum",
            "auto",
            "auto = copy streams when the target container allows it, otherwise re-encode.",
            choices=("auto", "reencode"),
        ),
    ]
    if crf is not None:
        opts.append(
            OptionSpec(
                "crf",
                "int",
                crf,
                "Constant quality (lower is better and larger).",
                0,
                63 if t == "webm" else 51,
            )
        )
    else:
        opts.append(OptionSpec("video_bitrate_kbps", "int", 2500, "Video bitrate in kbit/s.", 100, 50000))
    return tuple(opts)


def _streams(info: InputInfo, kind: str) -> list[dict[str, Any]]:
    """Streams of one kind; cover art (attached pictures) is never treated as video."""
    return [
        s
        for s in info.details.get("media", {}).get("streams", [])
        if s.get("codec_type") == kind and not s.get("attached_pic")
    ]


class FFmpegEngine(EngineAdapter):
    engine_id = "ffmpeg"

    def capabilities(self) -> list[Capability]:
        caps: list[Capability] = []
        for s in AUDIO:
            for t in AUDIO:
                caps.append(self._cap(s, t, Operation.TRANSFORM if s == t else Operation.CONVERT, "audio"))
        for s in VIDEO:
            for t in AUDIO:
                caps.append(self._cap(s, t, Operation.EXTRACT_AUDIO, "audio"))
            for t in VIDEO:
                caps.append(self._cap(s, t, Operation.TRANSFORM if s == t else Operation.CONVERT, "video"))
        return caps

    def _cap(self, s: str, t: str, op: Operation, kind: str) -> Capability:
        if kind == "audio":
            fid = Fidelity.LOSSY if t in LOSSY_AUDIO_TARGET else Fidelity.LOSSLESS_REENCODE
            opts = _audio_options(t)
            res = ResourceHint(
                cpu_heavy=False,
                memory_factor=0.5,
                memory_floor_mb=256,
                seconds_per_mb=0.05,
                indeterminate_progress=False,
            )
        else:
            fid = Fidelity.LOSSY
            opts = _video_options(t)
            res = ResourceHint(
                cpu_heavy=True,
                memory_factor=0.5,
                memory_floor_mb=512,
                seconds_per_mb=1.0,
                indeterminate_progress=False,
            )
        needs = ["exe:ffmpeg", f"encoder:{AUDIO_ENCODER.get(t, VIDEO_CODECS.get(t, ('',))[0])}"]
        return Capability(
            id=f"ffmpeg.{s}->{t}",
            engine_id=self.engine_id,
            source=s,
            target=t,
            operation=op,
            fidelity=fid,
            summary=(
                f"Re-encode {s.upper()} with new settings"
                if op is Operation.TRANSFORM
                else ("Extract audio from " if op is Operation.EXTRACT_AUDIO else "Transcode ")
                + f"{s.upper()} to {t.upper()}"
            ),
            options=opts,
            requires=tuple(needs),
            license=_LICENSE,
            resources=res,
            composable=False,
        )

    def availability(self, cap: Capability) -> Availability:
        if not which("ffmpeg") or not which("ffprobe"):
            return Availability(False, "ffmpeg/ffprobe are not installed.")
        enc = _encoders()
        needed = [r.split(":", 1)[1] for r in cap.requires if r.startswith("encoder:")]
        if cap.target in VIDEO:
            needed.append(VIDEO_CODECS[cap.target][1])
        missing = [n for n in needed if n and n not in enc]
        if missing:
            return Availability(False, f"ffmpeg lacks encoder(s): {', '.join(sorted(set(missing)))}.")
        return Availability(True, version=_version())

    # ------------------------------------------------------------------ assessment

    def assess(self, cap: Capability, info: InputInfo, options: dict[str, Any]) -> Assessment:
        t, s = cap.target, cap.source
        notes = list(cap.warnings)
        fid = cap.fidelity
        audio, video = _streams(info, "audio"), _streams(info, "video")
        if cap.target in AUDIO:
            if not audio:
                raise EZError(ErrorCode.UNSUPPORTED_INPUT, "This file has no audio stream to convert.")
            if s in LOSSLESS_AUDIO and t in LOSSLESS_AUDIO:
                fid = Fidelity.LOSSLESS_REENCODE
            elif s not in LOSSLESS_AUDIO and t in LOSSLESS_AUDIO:
                notes.append(
                    "The source is lossy; the lossless output preserves the decoded audio but cannot restore lost quality."
                )
            elif s not in LOSSLESS_AUDIO and t in LOSSY_AUDIO_TARGET:
                notes.append("Lossy-to-lossy conversion adds another generation of quality loss.")
            if any(x.get("attached_pic") for x in info.details.get("media", {}).get("streams", [])):
                notes.append("Embedded cover art is not carried over.")
            if cap.operation is Operation.EXTRACT_AUDIO and len(audio) > 1:
                notes.append("Only the first audio track is extracted.")
        else:
            if not video:
                raise EZError(ErrorCode.UNSUPPORTED_INPUT, "This file has no video stream to convert.")
            if self._can_copy(cap, info, options):
                fid = Fidelity.LOSSLESS_REENCODE
                notes.append("Streams are copied without re-encoding (container change only).")
            else:
                notes.append("Video is re-encoded; some quality loss is unavoidable.")
            if options.get("subtitles") == "keep":
                subs = _streams(info, "subtitle")
                if subs and t in ("avi", "mpeg"):
                    notes.append(f"{t.upper()} cannot hold subtitle tracks; they are dropped.")
                elif any(s_.get("codec_name") in BITMAP_SUBS for s_ in subs):
                    notes.append(
                        "Image-based subtitle tracks (PGS/VobSub) cannot be converted and are dropped."
                    )
            if len(audio) > 1 or len(video) > 1:
                notes.append("Only the first video and first audio track are kept.")
        return Assessment(fid, list(dict.fromkeys(notes)))

    @staticmethod
    def _can_copy(cap: Capability, info: InputInfo, options: dict[str, Any]) -> bool:
        if (
            cap.source == cap.target
            or options.get("mode") == "reencode"
            or options.get("max_height")
            or options.get("fps")
        ):
            return False
        ok_v, ok_a = COPY_OK.get(cap.target, (set(), set()))
        v, a = _streams(info, "video"), _streams(info, "audio")
        if not v or v[0].get("codec_name") not in ok_v:
            return False
        return not a or a[0].get("codec_name") in ok_a

    # ------------------------------------------------------------------ execution

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        cap, info, o = ctx.capability, ctx.input, ctx.options
        t = cap.target
        out = ctx.output_path(OUT_EXT.get(t, t))
        ffmpeg = which("ffmpeg")
        assert ffmpeg
        argv = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-protocol_whitelist",
            "file",
            "-threads",
            str(ctx.threads),
            "-fflags",
            "+genpts",
            "-f",
            INPUT_DEMUXER[info.format],
            "-i",
            str(info.path),
        ]
        warns: list[str] = []
        argv += self._audio_args(cap, info, o) if t in AUDIO else self._video_args(cap, info, o, warns)
        argv += ["-map_metadata", "0", "-f", OUT_MUXER[t], "-progress", "pipe:1", "-nostats", "-n", str(out)]

        duration = info.details.get("media", {}).get("duration_seconds") or 0.0

        def on_line(line: str) -> None:
            if line.startswith("out_time_us=") and duration:
                try:
                    ctx.progress(min(0.99, int(line.split("=", 1)[1]) / 1e6 / duration), "encoding")
                except ValueError:
                    pass

        ctx.progress(0.0, "encoding")
        r = run_process(
            argv,
            workdir=ctx.work_dir,
            limits=ctx.limits,
            cancel=ctx.cancel,
            on_line=on_line,
            capture_stdout=False,
            sandboxed=False,
        )
        if r.cancelled:
            raise EZError(ErrorCode.CANCELLED, "The conversion was cancelled.")
        if r.timed_out:
            raise EZError(ErrorCode.TIMEOUT, "The conversion took too long and was stopped.")
        if r.killed_by_signal:
            raise EZError(ErrorCode.RESOURCE_LIMIT, "The conversion exceeded its resource limits.")
        if r.returncode != 0 or not out.exists():
            raise EZError(
                ErrorCode.CONVERSION_FAILED,
                "FFmpeg could not convert this file.",
                {"engine_message": _clean_stderr(r.stderr_tail)},
            )
        return ExecutionResult(out, warns, {"ffmpeg": _version() or "unknown"})

    def _audio_args(self, cap: Capability, info: InputInfo, o: dict[str, Any]) -> list[str]:
        t = cap.target
        enc = AUDIO_ENCODER[t]
        a = ["-vn", "-map", f"0:{_streams(info, 'audio')[0]['index']}"]
        if t in ("wav", "aiff") and o.get("bit_depth") == 24:
            enc = "pcm_s24le" if t == "wav" else "pcm_s24be"
        a += ["-c:a", enc]
        if t == "flac" and o.get("bit_depth") == 24:
            a += ["-sample_fmt", "s32"]
        elif t == "flac":
            a += ["-sample_fmt", "s16"]
        if t in LOSSY_AUDIO_TARGET:
            a += ["-b:a", f"{o['bitrate_kbps']}k"]
        if t == "mp3":
            a += ["-id3v2_version", "3"]
        if o.get("sample_rate"):
            a += ["-ar", str(o["sample_rate"])]
        if o.get("channels"):
            a += ["-ac", str(o["channels"])]
        return a

    def _video_args(self, cap: Capability, info: InputInfo, o: dict[str, Any], warns: list[str]) -> list[str]:
        t = cap.target
        vcodec, acodec, _crf = VIDEO_CODECS[t]
        has_audio = bool(_streams(info, "audio"))
        a = ["-map", f"0:{_streams(info, 'video')[0]['index']}"]
        if has_audio:
            a += ["-map", f"0:{_streams(info, 'audio')[0]['index']}"]
        subs = [s for s in _streams(info, "subtitle") if s.get("codec_name") not in BITMAP_SUBS]
        sub_codec = {"mp4": "mov_text", "mov": "mov_text", "mkv": "copy", "webm": "webvtt"}.get(t)
        if o["subtitles"] == "keep" and subs and sub_codec:
            a += ["-map", f"0:{subs[0]['index']}", "-c:s", sub_codec]
        if self._can_copy(cap, info, o):
            a += ["-c:v", "copy"] + (["-c:a", "copy"] if has_audio else [])
            if t == "mp4" or t == "mov":
                a += ["-movflags", "+faststart"]
            return a
        vf = []
        if o.get("max_height"):
            vf.append(f"scale=-2:'min(ih,{int(o['max_height'])})'")
        else:
            vf.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
        if o.get("fps"):
            vf.append(f"fps={int(o['fps'])}")
        a += ["-vf", ",".join(vf), "-c:v", vcodec]
        if vcodec == "libx264":
            a += ["-preset", "medium", "-crf", str(o["crf"]), "-pix_fmt", "yuv420p"]
        elif vcodec == "libvpx-vp9":
            a += [
                "-b:v",
                "0",
                "-crf",
                str(o["crf"]),
                "-deadline",
                "good",
                "-cpu-used",
                "4",
                "-row-mt",
                "1",
                "-pix_fmt",
                "yuv420p",
            ]
        elif vcodec == "mpeg4":
            a += ["-b:v", f"{o['video_bitrate_kbps']}k", "-vtag", "xvid", "-pix_fmt", "yuv420p"]
        else:
            a += ["-b:v", f"{o['video_bitrate_kbps']}k", "-pix_fmt", "yuv420p"]
        if has_audio:
            a += ["-c:a", acodec, "-b:a", f"{o['audio_bitrate_kbps']}k"]
        if t in ("mp4", "mov"):
            a += ["-movflags", "+faststart"]
        return a


def _clean_stderr(text: str) -> str:
    # Keep only the last few lines and strip filesystem paths.
    lines = [re.sub(r"/[^\s:]+", "<path>", ln) for ln in text.splitlines()[-4:]]
    return "\n".join(lines)[:600]
