"""Lance le service : python -m mimetic.web [--host 0.0.0.0] [--port 8765]."""

from __future__ import annotations

import argparse
import socket
from pathlib import Path

import uvicorn

from mimetic.web.server import create_app, default_data_dir


def lan_addresses() -> list[str]:
    addrs = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")):
                addrs.add(ip)
    except OSError:
        pass
    return sorted(addrs)


def main() -> None:
    ap = argparse.ArgumentParser(prog="mimetic.web")
    ap.add_argument("--host", default="0.0.0.0", help="0.0.0.0 = accessible sur le LAN ; 127.0.0.1 = cette machine seulement")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--export-dir", type=Path, default=None)
    args = ap.parse_args()

    app = create_app(args.data_dir, args.export_dir)
    print(f"Mimetic beta — données : {args.data_dir or default_data_dir()}")
    print(f"Exports : {app.state.project.export_dir}")
    print(f"Journaux : {default_data_dir().parent / '.engine-logs'}")
    print(f"  http://localhost:{args.port}")
    if args.host == "0.0.0.0":
        for ip in lan_addresses():
            print(f"  http://{ip}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
