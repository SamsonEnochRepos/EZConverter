"""Structured data conversion: JSON, YAML, CSV, XML.

Semantics are defined, not guessed:
* YAML is loaded with safe_load only; timestamps become ISO strings (warned), non-string keys become
  strings (warned), unrepresentable values fail.
* JSON <-> CSV requires a list of flat objects. CSV carries no types: values are strings unless
  `infer_types` is set when reading.
* JSON -> XML uses the documented "typed XML" dialect (round-trips exactly through XML -> JSON);
  arbitrary XML -> JSON uses the generic mapping (attributes as "@name", text as "#text") and is
  reported as a semantic transformation.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
from collections.abc import Iterator
from typing import Any
from xml.etree import ElementTree as ET

import yaml
from defusedxml import ElementTree as DET
from defusedxml.common import DefusedXmlException

from ..domain.capability import Capability, Fidelity, LicenseInfo, Operation, OptionSpec, ResourceHint
from ..domain.errors import ErrorCode, EZError
from .base import Availability, Check, EngineAdapter, ExecutionContext, ExecutionResult

PAIRS = [
    ("json", "yaml"),
    ("yaml", "json"),
    ("json", "csv"),
    ("csv", "json"),
    ("json", "xml"),
    ("xml", "json"),
]
MAX_INPUT_MB = 128
MAX_DEPTH = 200
MAX_NODES = (
    5_000_000  # expansion budget: stops alias ("billion laughs") bombs long before memory is exhausted
)
DIALECT_ATTR = "ez-format"
DIALECT = "typed-json-1"
_LICENSE = LicenseInfo("PyYAML + defusedxml + stdlib", "MIT / PSF-2.0")
XML_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


class _Warn(list):
    def once(self, msg: str) -> None:
        if msg not in self:
            self.append(msg)


class _Budget:
    def __init__(self, limit: int = MAX_NODES):
        self.left = limit

    def spend(self, n: int = 1) -> None:
        self.left -= n
        if self.left < 0:
            raise EZError(
                ErrorCode.UNSAFE_INPUT,
                "The data expands to an unreasonable number of values (possible alias/entity bomb) and was rejected.",
            )


# ---------------------------------------------------------------------------- loading


def _read_text(path) -> str:
    try:
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EZError(ErrorCode.MALFORMED_INPUT, "The file is not valid UTF-8 text.") from exc


def _load_json(path) -> Any:
    try:
        return json.loads(_read_text(path), parse_constant=_reject_constant)
    except RecursionError as exc:
        raise EZError(ErrorCode.MALFORMED_INPUT, "The JSON is nested too deeply.") from exc
    except ValueError as exc:
        raise EZError(ErrorCode.MALFORMED_INPUT, f"Invalid JSON: {_short(exc)}") from exc


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def _load_yaml(path, warns: _Warn) -> Any:
    text = _read_text(path)
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        raise EZError(ErrorCode.MALFORMED_INPUT, f"Invalid YAML: {_short(exc)}") from exc
    except RecursionError as exc:
        raise EZError(ErrorCode.MALFORMED_INPUT, "The YAML is nested too deeply.") from exc
    if len(docs) == 0:
        raise EZError(ErrorCode.MALFORMED_INPUT, "The YAML file contains no document.")
    if len(docs) > 1:
        warns.once(f"The file has {len(docs)} YAML documents; they were combined into one JSON array.")
        return _jsonable(docs, warns)
    return _jsonable(docs[0], warns)


def _jsonable(v: Any, warns: _Warn, depth: int = 0, budget: _Budget | None = None) -> Any:
    budget = budget or _Budget()
    budget.spend()
    if depth > MAX_DEPTH:
        raise EZError(ErrorCode.MALFORMED_INPUT, "The data is nested too deeply.")
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise EZError(ErrorCode.CONVERSION_FAILED, "NaN and infinity cannot be represented in JSON.")
        return v
    if isinstance(v, (dt.datetime, dt.date)):
        warns.once("YAML timestamps were converted to ISO-8601 strings.")
        return v.isoformat()
    if isinstance(v, dict):
        out = {}
        for k, val in v.items():
            if not isinstance(k, str):
                if isinstance(k, (int, float, bool, dt.date)) or k is None:
                    warns.once("Non-string mapping keys were converted to strings.")
                    k = json.dumps(k) if not isinstance(k, (dt.date,)) else k.isoformat()
                else:
                    raise EZError(
                        ErrorCode.CONVERSION_FAILED,
                        "A mapping key is a complex value that JSON cannot represent.",
                    )
            out[k] = _jsonable(val, warns, depth + 1, budget)
        return out
    if isinstance(v, (list, tuple)):
        return [_jsonable(x, warns, depth + 1, budget) for x in v]
    raise EZError(
        ErrorCode.CONVERSION_FAILED, f"A value of type {type(v).__name__} cannot be represented in JSON."
    )


# ---------------------------------------------------------------------------- writers (size-capped)


def _write_capped(path, chunks: Iterator[str], cap_bytes: int) -> None:
    written = 0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        for c in chunks:
            written += len(c)
            if written > cap_bytes:
                raise EZError(
                    ErrorCode.UNSAFE_INPUT,
                    "The converted data would be unreasonably large (possible expansion attack).",
                )
            fh.write(c)


def _json_chunks(data: Any, indent: int | None) -> Iterator[str]:
    try:
        yield from json.JSONEncoder(indent=indent, ensure_ascii=False, allow_nan=False).iterencode(data)
    except RecursionError as exc:
        raise EZError(ErrorCode.MALFORMED_INPUT, "The data is nested too deeply.") from exc
    yield "\n"


# ---------------------------------------------------------------------------- csv


def _flat_scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def _to_csv(data: Any, opts: dict[str, Any], warns: _Warn) -> Iterator[str]:
    if isinstance(data, dict):
        data = [data]
        warns.once("The JSON object was treated as a single row.")
    if not isinstance(data, list) or not data or not all(isinstance(r, dict) for r in data):
        raise EZError(
            ErrorCode.CONVERSION_FAILED, "CSV output needs a JSON array of objects (one object per row)."
        )
    cols: dict[str, None] = {}
    for r in data:
        for k in r:
            cols.setdefault(k, None)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=opts["delimiter"], lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(list(cols))
    yield _drain(buf)
    nested = False
    for r in data:
        row = []
        for c in cols:
            v = r.get(c)
            if not _flat_scalar(v):
                if opts["nested"] == "error":
                    raise EZError(
                        ErrorCode.CONVERSION_FAILED,
                        f"Column '{c}' holds nested data, which CSV cannot represent. "
                        "Set option 'nested' to 'json' to store it as JSON text.",
                    )
                v = json.dumps(v, ensure_ascii=False)
                nested = True
            elif v is None:
                v = ""
            elif isinstance(v, bool):
                v = "true" if v else "false"
            if (
                opts["neutralize_formulas"]
                and isinstance(v, str)
                and v[:1] in ("=", "+", "-", "@", "\t", "\r")
            ):
                v = "'" + v
            row.append(v)
        w.writerow(row)
        yield _drain(buf)
    if nested:
        warns.once("Nested values were stored as JSON text inside CSV cells.")


def _drain(buf: io.StringIO) -> str:
    s = buf.getvalue()
    buf.seek(0)
    buf.truncate()
    return s


def _from_csv(path, opts: dict[str, Any], warns: _Warn) -> list[Any]:
    text = _read_text(path)
    delim = opts["delimiter"]
    if delim == "auto":
        try:
            delim = csv.Sniffer().sniff(text[:65536], delimiters=",;\t|").delimiter
        except csv.Error:
            delim = ","
    csv.field_size_limit(16 * 1024 * 1024)
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delim, strict=True))
    except csv.Error as exc:
        raise EZError(ErrorCode.MALFORMED_INPUT, f"Invalid CSV: {_short(exc)}") from exc
    rows = [r for r in rows if r]
    if not rows:
        raise EZError(ErrorCode.MALFORMED_INPUT, "The CSV file is empty.")
    if opts["header"]:
        head, body = rows[0], rows[1:]
        if len(set(head)) != len(head):
            raise EZError(
                ErrorCode.CONVERSION_FAILED,
                "CSV header row has duplicate column names; JSON objects need unique keys.",
            )
    else:
        head, body = [f"column{i + 1}" for i in range(len(rows[0]))], rows
    out = []
    for i, r in enumerate(body, start=2 if opts["header"] else 1):
        if len(r) != len(head):
            raise EZError(
                ErrorCode.MALFORMED_INPUT, f"CSV row {i} has {len(r)} fields but the header has {len(head)}."
            )
        out.append({h: (_infer(v) if opts["infer_types"] else v) for h, v in zip(head, r)})
    if opts["infer_types"]:
        warns.once(
            "Types were inferred from text (numbers, true/false, empty→null); verify before relying on them."
        )
    return out


def _infer(v: str) -> Any:
    if v == "":
        return None
    if v in ("true", "false"):
        return v == "true"
    if re.fullmatch(r"-?(0|[1-9]\d*)", v):
        return int(v)
    if re.fullmatch(r"-?(0|[1-9]\d*)\.\d+([eE][+-]?\d+)?", v):
        return float(v)
    return v


# ---------------------------------------------------------------------------- xml


def _typed_xml(data: Any) -> ET.Element:
    root = ET.Element("root", {DIALECT_ATTR: DIALECT})
    _fill(root, data, 0)
    return root


def _fill(el: ET.Element, v: Any, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise EZError(ErrorCode.MALFORMED_INPUT, "The data is nested too deeply.")
    if v is None:
        el.set("type", "null")
    elif isinstance(v, bool):
        el.set("type", "boolean")
        el.text = "true" if v else "false"
    elif isinstance(v, (int, float)):
        el.set("type", "number")
        el.text = json.dumps(v)
    elif isinstance(v, str):
        el.set("type", "string")
        el.text = _xml_safe(v)
    elif isinstance(v, list):
        el.set("type", "array")
        for x in v:
            _fill(ET.SubElement(el, "item"), x, depth + 1)
    elif isinstance(v, dict):
        el.set("type", "object")
        for k, x in v.items():
            if not XML_NAME.match(k):
                # keys that are not valid element names are carried in an attribute
                child = ET.SubElement(el, "entry", {"key": _xml_safe(k)})
            else:
                child = ET.SubElement(el, k)
            _fill(child, x, depth + 1)


def _xml_safe(s: str) -> str:
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]", s):
        raise EZError(
            ErrorCode.CONVERSION_FAILED, "A string contains control characters that XML 1.0 cannot represent."
        )
    return s


def _from_typed(el: ET.Element) -> Any:
    t = el.get("type")
    if t == "null":
        return None
    if t == "boolean":
        return (el.text or "") == "true"
    if t == "number":
        return json.loads(el.text or "0")
    if t == "string":
        return el.text or ""
    if t == "array":
        return [_from_typed(c) for c in el]
    if t == "object":
        out = {}
        for c in el:
            out[c.get("key") if c.tag == "entry" and c.get("key") is not None else c.tag] = _from_typed(c)
        return out
    raise EZError(ErrorCode.CONVERSION_FAILED, f"Unknown value type '{t}' in typed XML.")


def _generic_xml(el: ET.Element, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        raise EZError(ErrorCode.MALFORMED_INPUT, "The XML is nested too deeply.")
    node: dict[str, Any] = {f"@{k}": v for k, v in el.attrib.items()}
    children: dict[str, list[Any]] = {}
    for c in el:
        children.setdefault(_local(c.tag), []).append(_generic_xml(c, depth + 1))
    for k, vs in children.items():
        node[k] = vs[0] if len(vs) == 1 else vs
    text = (el.text or "").strip()
    if text:
        if not node:
            return text
        node["#text"] = text
    return node or None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ---------------------------------------------------------------------------- engine


class StructuredEngine(EngineAdapter):
    engine_id = "structured"

    def capabilities(self) -> list[Capability]:
        caps = []
        for s, t in PAIRS:
            opts: list[OptionSpec] = []
            warn: tuple[str, ...] = ()
            fid = Fidelity.LOSSLESS_REENCODE
            if t == "json":
                opts.append(OptionSpec("indent", "int", 2, "Indent width (0 = compact).", 0, 8))
            if t == "yaml":
                warn = ("Comments and key formatting from the source are not preserved (JSON has none).",)
            if t == "csv":
                opts += [
                    OptionSpec("delimiter", "enum", ",", "Field delimiter.", choices=(",", ";", "\t", "|")),
                    OptionSpec(
                        "nested", "enum", "error", "How to handle nested values.", choices=("error", "json")
                    ),
                    OptionSpec(
                        "neutralize_formulas",
                        "bool",
                        False,
                        "Prefix cells starting with = + - @ with ' to prevent spreadsheet formula injection.",
                    ),
                ]
                fid = Fidelity.LOSSY
                warn = (
                    "CSV has no types: numbers, booleans and null become text; null becomes an empty cell.",
                )
            if s == "csv":
                opts += [
                    OptionSpec(
                        "delimiter", "enum", "auto", "Field delimiter.", choices=("auto", ",", ";", "\t", "|")
                    ),
                    OptionSpec("header", "bool", True, "First row contains column names."),
                    OptionSpec(
                        "infer_types", "bool", False, "Convert numeric/boolean-looking text to JSON types."
                    ),
                ]
            if s == "yaml":
                warn = ("YAML-only features (comments, anchors, custom tags) are resolved or dropped.",)
            if (s, t) == ("json", "xml"):
                fid = Fidelity.SEMANTIC
                warn = (
                    "Uses the EZConverter typed-XML dialect so the result converts back to identical JSON.",
                )
            if (s, t) == ("xml", "json"):
                fid = Fidelity.SEMANTIC
                warn = (
                    "Generic mapping: attributes become '@name', text '#text', repeated siblings arrays. "
                    "Ordering of differently named siblings and mixed content are not preserved.",
                )
            caps.append(
                Capability(
                    id=f"structured.{s}->{t}",
                    engine_id=self.engine_id,
                    source=s,
                    target=t,
                    operation=Operation.CONVERT,
                    fidelity=fid,
                    summary=f"Convert {s.upper()} to {t.upper()}",
                    options=tuple(opts),
                    requires=("py:yaml", "py:defusedxml"),
                    warnings=warn,
                    license=_LICENSE,
                    resources=ResourceHint(memory_factor=15, seconds_per_mb=0.5, max_input_mb=MAX_INPUT_MB),
                    composable=("json" in (s, t)) and (s, t) != ("xml", "json"),
                )
            )
        return caps

    def availability(self, cap: Capability) -> Availability:
        return Availability(True, version=f"PyYAML {yaml.__version__}")

    def _load(self, ctx: ExecutionContext, warns: _Warn) -> Any:
        s, o = ctx.input.format, ctx.options
        p = ctx.input.path
        if s == "json":
            return _load_json(p)
        if s == "yaml":
            return _load_yaml(p, warns)
        if s == "csv":
            return _from_csv(p, o, warns)
        if s == "xml":
            try:
                root = DET.fromstring(p.read_bytes())
            except DefusedXmlException as exc:
                raise EZError(
                    ErrorCode.MALFORMED_INPUT,
                    "This XML uses DTDs, entities or external references, which are not allowed for safety.",
                ) from exc
            except ET.ParseError as exc:
                raise EZError(
                    ErrorCode.MALFORMED_INPUT,
                    f"Invalid XML at line {exc.position[0]}, column {exc.position[1]}.",
                ) from exc
            if root.get(DIALECT_ATTR) == DIALECT:
                return _from_typed(root)
            warns.once("Generic XML mapping used; see capability notes.")
            return {_local(root.tag): _generic_xml(root)}
        raise AssertionError(s)

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        t, o = ctx.capability.target, ctx.options
        cap_bytes = ctx.limits.output_file_mb * 1024 * 1024
        warns = _Warn()
        ctx.progress(None, "parsing")
        data = self._load(ctx, warns)
        out = ctx.output_path(t if t != "yaml" else "yaml")
        ctx.progress(None, "writing")
        try:
            if t == "json":
                _write_capped(out, _json_chunks(data, o["indent"] or None), cap_bytes)
            elif t == "yaml":
                text = yaml.safe_dump(
                    data, sort_keys=False, allow_unicode=True, default_flow_style=False, width=120
                )
                _write_capped(out, iter([text]), cap_bytes)
            elif t == "csv":
                _write_capped(out, _to_csv(data, o, warns), cap_bytes)
            elif t == "xml":
                root = _typed_xml(data)
                xml = ET.tostring(root, encoding="unicode")
                _write_capped(out, iter(['<?xml version="1.0" encoding="UTF-8"?>\n', xml, "\n"]), cap_bytes)
        except RecursionError as exc:
            raise EZError(ErrorCode.MALFORMED_INPUT, "The data is nested too deeply.") from exc
        return ExecutionResult(out, list(warns), {"pyyaml": yaml.__version__}, {})

    def extra_checks(self, ctx: ExecutionContext, result: ExecutionResult) -> list[Check]:
        """Independent semantic verification: re-read the output and compare with the source data."""
        s, t = ctx.capability.source, ctx.capability.target
        try:
            if t == "yaml" or (s, t) == ("json", "xml"):
                src = _load_json(ctx.input.path) if s == "json" else None
                if t == "yaml":
                    back = _jsonable(yaml.safe_load(result.output_path.read_text("utf-8")), _Warn())
                else:
                    back = _from_typed(DET.fromstring(result.output_path.read_bytes()))
                return [
                    Check(
                        "roundtrip_equal",
                        src == back,
                        "parsed output equals the source data"
                        if src == back
                        else "parsed output differs from the source data",
                    )
                ]
            if (s, t) == ("yaml", "json"):
                docs = list(yaml.safe_load_all(ctx.input.path.read_text("utf-8-sig")))
                expected = _jsonable(docs[0] if len(docs) == 1 else docs, _Warn())
                got = json.loads(result.output_path.read_text("utf-8"))
                return [Check("roundtrip_equal", expected == got, "JSON output equals the loaded YAML data")]
            if (s, t) == ("json", "csv"):
                rows = list(
                    csv.reader(
                        io.StringIO(result.output_path.read_text("utf-8"), newline=""),
                        delimiter=ctx.options["delimiter"],
                    )
                )
                src = _load_json(ctx.input.path)
                n = 1 if isinstance(src, dict) else len(src)
                return [Check("row_count", len(rows) - 1 == n, f"{len(rows) - 1} data row(s), expected {n}")]
            if (s, t) == ("csv", "json"):
                out = json.loads(result.output_path.read_text("utf-8"))
                want = len(_from_csv(ctx.input.path, ctx.options, _Warn()))
                return [
                    Check(
                        "row_count",
                        isinstance(out, list) and len(out) == want,
                        f"{len(out)} record(s), expected {want}",
                    )
                ]
        except Exception as exc:
            return [Check("semantic_verification", False, f"Verification failed: {_short(exc)}")]
        return []


def _short(exc: BaseException) -> str:
    return re.sub(r"\s+", " ", str(exc))[:160]
