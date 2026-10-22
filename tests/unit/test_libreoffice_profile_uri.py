from pathlib import PurePosixPath, PureWindowsPath

from ezconverter.engines.documents import _user_installation_arg


class _FakePath:
    """Stands in for Path with a pure path flavour so both platforms can be checked on any OS."""

    def __init__(self, pure):
        self._pure = pure

    def absolute(self):
        return self._pure


def test_windows_profile_becomes_a_valid_file_uri():
    arg = _user_installation_arg(_FakePath(PureWindowsPath("C:/Users/me/work/lo-profile")))
    assert arg == "-env:UserInstallation=file:///C:/Users/me/work/lo-profile"


def test_posix_profile_keeps_three_slash_uri():
    arg = _user_installation_arg(_FakePath(PurePosixPath("/tmp/job 1/lo-profile")))
    assert arg == "-env:UserInstallation=file:///tmp/job%201/lo-profile"


def test_real_path_yields_absolute_file_uri(tmp_path):
    arg = _user_installation_arg(tmp_path / "lo-profile")
    assert arg.startswith("-env:UserInstallation=file:///")
    assert arg.endswith("/lo-profile")


def test_execute_passes_the_file_uri_to_soffice(tmp_path, monkeypatch):
    import threading

    from ezconverter.engines import documents
    from ezconverter.engines.base import ExecutionContext, InputInfo
    from ezconverter.sandbox import Limits, ProcResult

    seen: list[list[str]] = []

    def fake_run(argv, **_kw):
        seen.append(list(argv))
        outdir = argv[argv.index("--outdir") + 1]
        (tmp_path / outdir).joinpath("input.pdf").write_bytes(b"%PDF-")
        return ProcResult(returncode=0)

    monkeypatch.setattr(documents, "which", lambda _name: "soffice")
    monkeypatch.setattr(documents, "run_process", fake_run)
    src = tmp_path / "a.docx"
    src.write_bytes(b"x")
    (tmp_path / "work").mkdir()
    (tmp_path / "out").mkdir()
    cap = next(c for c in documents.LibreOfficeEngine().capabilities() if c.id == "libreoffice.docx->pdf")
    ctx = ExecutionContext(
        capability=cap,
        input=InputInfo(src, "a.docx", "docx", 1),
        options={},
        output_dir=tmp_path / "out",
        work_dir=tmp_path / "work",
        output_stem="a",
        limits=Limits(),
        cancel=threading.Event(),
    )
    documents.LibreOfficeEngine().execute(ctx)
    arg = next(a for a in seen[0] if a.startswith("-env:UserInstallation="))
    uri = arg.split("=", 1)[1]
    assert uri == (tmp_path / "work" / "lo-profile").absolute().as_uri()
    assert uri.startswith("file:///")
