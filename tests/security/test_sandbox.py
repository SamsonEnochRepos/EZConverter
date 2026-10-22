import sys
import threading
import time

import pytest

from ezconverter.sandbox import Limits, network_isolation_available, run_process


def test_rejects_shell_strings(tmp_path):
    with pytest.raises(TypeError):
        run_process("echo hi; rm -rf /", workdir=tmp_path)  # type: ignore[arg-type]


def test_arguments_are_never_shell_interpreted(tmp_path):
    marker = tmp_path / "pwned"
    r = run_process(
        ["/bin/echo", f"; touch {marker}; $(touch {marker}) `touch {marker}`"],
        workdir=tmp_path,
        sandboxed=False,
    )
    assert r.returncode == 0 and not marker.exists() and "touch" in r.stdout


def test_wall_clock_timeout_kills_process_group(tmp_path):
    t = time.time()
    r = run_process(
        ["/bin/sh", "-c", "sleep 30 & sleep 30"],
        workdir=tmp_path,
        limits=Limits(wall_seconds=1.0),
        sandboxed=False,
    )
    assert r.timed_out and time.time() - t < 6


def test_cancellation_kills_promptly(tmp_path):
    ev = threading.Event()
    threading.Timer(0.5, ev.set).start()
    t = time.time()
    r = run_process(["/bin/sleep", "30"], workdir=tmp_path, cancel=ev, sandboxed=False)
    assert r.cancelled and time.time() - t < 5


def test_environment_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("EZ_API_KEYS", "super-secret:alice")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak")
    r = run_process(["/usr/bin/env"], workdir=tmp_path, sandboxed=False)
    assert "super-secret" not in r.stdout and "AWS_SECRET" not in r.stdout and "PATH=" in r.stdout


@pytest.mark.skipif(not network_isolation_available(), reason="network namespaces unavailable")
def test_network_namespace_blocks_loopback_connections(tmp_path):
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    code = f"import socket,sys\ntry:\n socket.create_connection(('127.0.0.1',{port}),timeout=2); print('open')\nexcept OSError: print('blocked')"
    open_r = run_process(
        [sys.executable, "-I", "-c", code],
        workdir=tmp_path,
        limits=Limits(isolate_network=False),
    )
    blocked = run_process(
        [sys.executable, "-I", "-c", code],
        workdir=tmp_path,
        limits=Limits(isolate_network=True),
    )
    srv.close()
    assert open_r.stdout.strip() == "open" and blocked.stdout.strip() == "blocked"


def test_memory_limit_is_enforced(tmp_path):
    r = run_process(
        [
            sys.executable,
            "-I",
            "-c",
            "x = bytearray(900*1024*1024); print('allocated')",
        ],
        workdir=tmp_path,
        limits=Limits(memory_mb=300, isolate_network=False),
    )
    assert "allocated" not in r.stdout and r.returncode != 0


def test_output_file_size_limit_is_enforced(tmp_path):
    code = "f=open('big','wb')\nfor _ in range(50): f.write(b'0'*1024*1024)\nf.close()\nprint('done')"
    r = run_process(
        [sys.executable, "-I", "-c", code],
        workdir=tmp_path,
        limits=Limits(output_file_mb=5, isolate_network=False),
    )
    assert "done" not in r.stdout and (tmp_path / "big").stat().st_size <= 5 * 1024 * 1024


def test_network_isolation_modes_fail_closed_or_warn(tmp_path, monkeypatch, caplog):
    import pytest as _pytest

    from ezconverter.api.app import isolation_state
    from ezconverter.config import Settings

    monkeypatch.setattr("ezconverter.sandbox.network_isolation_available", lambda: False)
    base = Settings(data_dir=tmp_path)
    base.network_isolation = "require"
    with _pytest.raises(RuntimeError, match="cannot create network namespaces"):
        isolation_state(base)  # a missing control must stop startup, not vanish
    base.network_isolation = "auto"
    with caplog.at_level("WARNING"):
        assert isolation_state(base) == "unavailable"
    assert "UNAVAILABLE" in caplog.text
    base.network_isolation = "off"
    assert isolation_state(base) == "disabled"
    base.network_isolation = "bogus"
    with _pytest.raises(RuntimeError):
        isolation_state(base)
    monkeypatch.setattr("ezconverter.sandbox.network_isolation_available", lambda: True)
    base.network_isolation = "require"
    assert isolation_state(base) == "active"
