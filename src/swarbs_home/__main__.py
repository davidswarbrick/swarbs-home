"""Command line entry point.

    swarbs-home                 # serve with defaults
    swarbs-home serve --port 8000 --config ./swarbs-home.conf
    swarbs-home record status|start|stop [--label NAME]
"""

from __future__ import annotations

import argparse
import json
import sys

from .app import create_app
from .config import load_config
from .recorder import Recorder, RecorderError


def _serve(args) -> int:
    cfg = load_config(args.config)
    host = args.host or cfg.server["host"]
    port = args.port or int(cfg.server["port"])
    app = create_app(cfg)

    print(f"swarbs-home: config={cfg.source} serving http://{host}:{port}", flush=True)
    if args.debug:
        app.run(host=host, port=port, debug=True)
    else:
        from waitress import serve
        serve(app, host=host, port=port)
    return 0


def _record(args) -> int:
    cfg = load_config(args.config)
    rec = Recorder(cfg.recorder)
    if args.action == "status":
        print(json.dumps(rec.status(), indent=2))
        return 0
    try:
        if args.action == "start":
            print("recording ->", rec.start(args.label))
        elif args.action == "stop":
            rec.stop()
            print("stopped")
    except RecorderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="swarbs-home")
    sub = parser.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="run the web server (default)")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--config")
    serve.add_argument("--debug", action="store_true")

    record = sub.add_parser("record", help="control recording from the CLI")
    record.add_argument("action", choices=["status", "start", "stop"])
    record.add_argument("--label")
    record.add_argument("--config")

    args = parser.parse_args(argv)

    if args.cmd == "record":
        return _record(args)
    # default (no subcommand) and "serve" both serve
    if args.cmd is None:
        args = parser.parse_args(["serve", *(argv or [])])
    return _serve(args)


if __name__ == "__main__":
    raise SystemExit(main())
