import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest


@pytest.fixture
def fake_bybit():
    """A local HTTP server that answers every request with what a test puts in ``state``.

    ``state["body"]`` is a dict sent as JSON, or bytes sent as they are; ``state["status"]``
    is the HTTP status. Like Bybit, a JSON list of rows is cut to the request's ``limit``.
    """
    state = {"body": None, "status": 200, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append(self.path)
            body = state["body"]
            if isinstance(body, bytes):
                payload, kind = body, "text/html"
            else:
                limit = parse_qs(urlsplit(self.path).query).get("limit")
                if limit and "result" in body:
                    rows = body["result"]["list"][:int(limit[0])]
                    body = {**body, "result": {**body["result"], "list": rows}}
                payload, kind = json.dumps(body).encode(), "application/json"
            self.send_response(state["status"])
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):    # keep pytest output clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()
    server.server_close()
