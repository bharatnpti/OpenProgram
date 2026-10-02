"""Forward only chat webhooks to the local backend, for a public tunnel to point at.

    uv run python -m scripts.qa_org.webhook_gate               # 127.0.0.1:8787 -> :8000
    cloudflared tunnel --url http://127.0.0.1:8787

Under dev auth every other route answers as an admin with no credentials, so
tunnelling the backend port directly would publish the whole config API. This
gate passes ``POST /webhooks/chat/<provider>`` through byte-for-byte (Slack signs the raw
body) and returns 404 for everything else.
"""

from __future__ import annotations

import argparse
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

# An exact shape, not a prefix: "/webhooks/../config/..." must not slip through
# a startswith check and be normalised into an admin route upstream.
ALLOWED_PATH = re.compile(r"/webhooks/chat/[a-z_]+")
FORWARDED_HEADERS = frozenset(
    {
        "content-type",
        "user-agent",
        "x-slack-signature",
        "x-slack-request-timestamp",
        "x-slack-retry-num",
        "x-slack-retry-reason",
    }
)


def make_handler(upstream: str) -> type[BaseHTTPRequestHandler]:
    class Gate(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server naming
            if not ALLOWED_PATH.fullmatch(self.path):
                self.send_error(404)
                return
            body = self.rfile.read(int(self.headers.get("content-length") or 0))
            headers = {k: v for k, v in self.headers.items() if k.lower() in FORWARDED_HEADERS}
            try:
                response = httpx.post(
                    upstream + self.path, content=body, headers=headers, timeout=30
                )
            except httpx.HTTPError as exc:
                self.send_error(502, f"backend unreachable: {type(exc).__name__}")
                return
            self.send_response(response.status_code)
            self.send_header("content-type", response.headers.get("content-type", "text/plain"))
            self.send_header("content-length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            self.send_error(404)

    return Gate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--upstream", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.upstream.rstrip("/")))
    print(f"forwarding POST /webhooks/chat/<provider> on :{args.port} -> {args.upstream}")
    server.serve_forever()


if __name__ == "__main__":
    main()
