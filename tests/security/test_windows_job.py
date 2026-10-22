"""Windows Job Object confinement (the Windows counterpart of the prlimit/unshare tests)."""

import sys
import time

import pytest

from ezconverter.sandbox import Limits, run_process

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Job Objects exist only on Windows")


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_memory_limit_stops_a_runaway_allocation(tmp_path):
    r = run_process(
        _py("x = bytearray(900 * 1024 * 1024); x[::4096] = b'1' * len(x[::4096]); print('survived')"),
        workdir=tmp_path,
        limits=Limits(memory_mb=300, wall_seconds=60),
    )
    assert "survived" not in r.stdout and r.returncode != 0


def test_normal_work_under_the_limits_succeeds(tmp_path):
    r = run_process(_py("print(sum(range(1000)))"), workdir=tmp_path, limits=Limits(memory_mb=512))
    assert r.returncode == 0 and r.stdout.strip() == "499500"


def test_timeout_kills_the_whole_process_tree(tmp_path):
    marker = tmp_path / "grandchild-survived"
    grandchild = f"import time,pathlib; time.sleep(4); pathlib.Path(r'{marker}').write_text('x')"
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{grandchild!r}]); time.sleep(60)"
    )
    t = time.time()
    r = run_process(_py(parent), workdir=tmp_path, limits=Limits(wall_seconds=1.5))
    assert r.timed_out and time.time() - t < 10
    time.sleep(5)
    assert not marker.exists(), "a descendant outlived the worker"


def test_process_count_is_capped(tmp_path):
    code = (
        "import subprocess,sys; ps=[]\n"
        "try:\n"
        "    for _ in range(64): ps.append(subprocess.Popen([sys.executable,'-c','import time; time.sleep(5)']))\n"
        "    print('spawned', len(ps))\n"
        "except OSError:\n"
        "    print('blocked', len(ps))\n"
        "for p in ps: p.kill()\n"
    )
    r = run_process(_py(code), workdir=tmp_path, limits=Limits(wall_seconds=60))
    assert r.stdout.startswith("blocked") or r.returncode != 0, r.stdout


def test_cpu_time_limit_ends_a_busy_loop(tmp_path):
    t = time.time()
    r = run_process(_py("while True: pass"), workdir=tmp_path, limits=Limits(cpu_seconds=2, wall_seconds=60))
    assert not r.timed_out and time.time() - t < 20 and r.returncode != 0


def test_worker_environment_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("EZ_API_KEYS", "supersecret:me")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "supersecret")
    r = run_process(_py("import os; print(sorted(os.environ))"), workdir=tmp_path)
    assert "supersecret" not in r.stdout and "EZ_API_KEYS" not in r.stdout and "AWS_SECRET_ACCESS_KEY" not in r.stdout

