"""TEST-ONLY engines, loaded through EZ_EXTRA_ENGINES by the test-suite. Never part of the default registry.

They use RTF -> {xml, yaml, csv, json, svg}, pairs no production engine supports, to exercise how the
orchestrator and sandbox behave when an engine is slow, crashes, hogs memory, emits invalid output,
probes the network or inspects its environment.
"""

from __future__ import annotations

import os
import signal
import socket
import time

from ezconverter.domain.capability import Capability, Fidelity, Operation, OptionSpec
from ezconverter.domain.errors import ErrorCode, EZError
from ezconverter.engines.base import (
    Availability,
    EngineAdapter,
    ExecutionContext,
    ExecutionResult,
)


class TestEngines(EngineAdapter):
    __test__ = False
    engine_id = "testengines"

    def capabilities(self):
        def cap(t, opts=()):
            return Capability(
                id=f"test.rtf->{t}",
                engine_id=self.engine_id,
                source="rtf",
                target=t,
                operation=Operation.CONVERT,
                fidelity=Fidelity.SEMANTIC,
                summary=f"test engine rtf->{t}",
                options=tuple(opts),
            )

        return [
            cap(
                "xml",
                [
                    OptionSpec("sleep_seconds", "int", 0, "sleep", 0, 600),
                    OptionSpec("probe_port", "int", 0, "port", 0, 65535),
                    OptionSpec(
                        "fail_first",
                        "bool",
                        False,
                        "fail on the first attempt via marker file",
                    ),
                ],
            ),
            cap("yaml"),
            cap("csv"),
            cap("json"),
            cap("svg"),
        ]

    def availability(self, cap):
        return Availability(True, version="test")

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        t = ctx.capability.target
        out = ctx.output_path(t)
        if t == "xml":
            o = ctx.options
            end = time.time() + o["sleep_seconds"]
            while time.time() < end:
                if ctx.cancel.is_set():
                    raise EZError(ErrorCode.CANCELLED, "cancelled")
                ctx.progress(1 - (end - time.time()) / max(1, o["sleep_seconds"]), "sleeping")
                time.sleep(0.1)
            net = "none"
            if o["probe_port"]:
                try:
                    socket.create_connection(("127.0.0.1", o["probe_port"]), timeout=2).close()
                    net = "open"
                except OSError:
                    net = "blocked"
            out.write_text(f'<?xml version="1.0"?><r net="{net}"/>')
        elif t == "yaml":
            os.kill(os.getpid(), signal.SIGSEGV)
        elif t == "csv":
            blob = bytearray(3 * 1024**3)  # exceeds the worker memory limit used in tests
            out.write_text(str(len(blob)))
        elif t == "json":
            out.write_text("{ this is not json")
        elif t == "svg":
            names = " ".join(sorted(os.environ))
            out.write_text(
                f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><text>{names}</text></svg>'
            )
        return ExecutionResult(out)
