"""Start the Options101 web app.

    python3 -m cryptoptions.app            # live Bybit data
    python3 -m cryptoptions.app --demo     # sample data, works offline
"""

from __future__ import annotations

import argparse
import threading
import webbrowser


def main() -> None:
    ap = argparse.ArgumentParser(prog="python3 -m cryptoptions.app", description="Options101 web app")
    ap.add_argument("--demo", action="store_true", help="use sample data instead of live Bybit prices")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = ap.parse_args()

    import uvicorn

    from .server import create_app

    url = f"http://{args.host}:{args.port}/"
    print(f"Options101 running at {url} ({'demo data' if args.demo else 'live Bybit data'}). Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(demo=args.demo), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
