"""Command line: `python -m ezconverter serve` and `python -m ezconverter openapi` (plus the internal `worker`)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import socket
import sys
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE lines from a .env file into the environment without overriding real variables."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = re.split(r"(?:^|\s)#", value.strip(), maxsplit=1)[0].strip().strip("\"'")
        os.environ.setdefault(key.strip(), value)


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if raw[:1] == ["worker"]:  # internal: sandboxed worker process of a packaged build (see workers/launcher.py)
        from .workers.entry import main as worker_main

        return worker_main(["entry", *raw[1:]])
    ap = argparse.ArgumentParser(prog="ezconverter")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the API server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787, help="0 = pick a free port (printed as a READY line)")
    s.add_argument(
        "--sidecar",
        action="store_true",
        help="desktop mode: bind loopback only, print `EZCONVERTER_READY {json}` once listening",
    )
    sub.add_parser("openapi", help="print the OpenAPI document as JSON")
    args = ap.parse_args(argv)

    from .api.app import create_app
    from .config import Settings

    load_dotenv()
    settings = Settings.from_env()
    if args.cmd == "openapi":
        import tempfile
        from pathlib import Path

        settings.data_dir = Path(tempfile.mkdtemp(prefix="ez-openapi-"))
        settings.auth_mode = "none"
        json.dump(create_app(settings).openapi(), sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.sidecar and args.host not in ("127.0.0.1", "::1", "localhost"):
        ap.error("--sidecar only binds to loopback")
    if settings.auth_mode == "token" and not settings.api_keys:
        logging.getLogger("ezconverter").warning(
            "EZ_AUTH_MODE=token but EZ_API_KEYS is empty: every request will be rejected"
        )
    app = create_app(settings)
    sock = socket.socket(socket.AF_INET6 if ":" in args.host else socket.AF_INET)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    sock.listen(128)
    port = sock.getsockname()[1]
    if args.sidecar or args.port == 0:
        print(
            "EZCONVERTER_READY "
            + json.dumps({"host": args.host, "port": port, "location": settings.location.value}),
            flush=True,
        )
    # Pass the socket object, not fd=: uvicorn rebuilds fd sockets as AF_UNIX, which Windows lacks.
    uvicorn.Server(uvicorn.Config(app, log_level="info", server_header=False)).run(sockets=[sock])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
