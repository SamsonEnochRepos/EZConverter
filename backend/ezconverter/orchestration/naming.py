"""Filename safety and collision handling."""

from __future__ import annotations

import re
import unicodedata

from ..domain.formats import FORMATS

_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_BAD = re.compile(r'[\x00-\x1f\x7f<>:"|?*\\/]')


def safe_filename(name: str, fallback: str = "file") -> str:
    """Strip directories and characters that are unsafe on common filesystems."""
    name = unicodedata.normalize("NFC", name or "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _BAD.sub("_", name).strip(" .")
    stem, dot, ext = name.rpartition(".") if "." in name else (name, "", "")
    if stem.lower() in _RESERVED:
        stem = "_" + stem
    stem = stem[:120] or fallback
    ext = ext[:16]
    return f"{stem}.{ext}" if dot and ext else stem


def stem_of(name: str) -> str:
    s = safe_filename(name)
    base = s.rsplit(".", 1)[0] if "." in s else s
    if s.lower().endswith(".tar.gz"):
        base = s[:-7]
    return base or "file"


def output_filename(original: str, target: str) -> str:
    ext = FORMATS[target].primary_extension if target != "jpeg" else "jpg"
    if target == "tgz":
        ext = "tar.gz"
    return f"{stem_of(original)}.{ext}"


def dedupe_names(names: list[str], reserved: tuple[str, ...] = ()) -> list[str]:
    """Resolve case-insensitive collisions deterministically: 'a.png', 'a (1).png', 'a (2).png'."""
    used: set[str] = {r.lower() for r in reserved}
    out = []
    for n in names:
        cand, i = n, 0
        while cand.lower() in used:
            i += 1
            if "." in n:
                stem, ext = n.rsplit(".", 1)
                cand = f"{stem} ({i}).{ext}"
            else:
                cand = f"{n} ({i})"
        used.add(cand.lower())
        out.append(cand)
    return out
