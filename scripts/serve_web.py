#!/usr/bin/env python
"""Range-capable local preview server for the Part 2-3 web map.

Stock ``python -m http.server`` does **not** honour HTTP Range requests, but the roof layer's
PMTiles are read by byte-range — so plain ``http.server`` serves a broken roof layer (the tract
layer still works). This serves ``web/`` with Range support, matching GitHub Pages' Fastly CDN in
production. Use it as the web app's local dev-preview.

    python scripts/serve_web.py                 # http://127.0.0.1:8000  (serves web/)
    python scripts/serve_web.py --port 8123
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import re
from pathlib import Path


class RangeHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Static handler that honours a single ``Range: bytes=start-end`` request (206 response)."""

    def do_GET(self):  # noqa: N802 (http.server API name)
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not rng or not os.path.isfile(path):
            return super().do_GET()

        size = os.path.getsize(path)
        m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
        if not m:
            self.send_error(400, "Invalid Range")
            return
        first, last = m.group(1), m.group(2)
        if first == "":  # suffix range: final `last` bytes
            length = int(last)
            start, end = max(0, size - length), size - 1
        else:
            start = int(first)
            end = int(last) if last else size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return

        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            self.wfile.write(f.read(length))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--directory", default=str(Path(__file__).resolve().parents[1] / "web"),
        help="directory to serve (default: web/)",
    )
    args = parser.parse_args()

    handler = functools.partial(RangeHTTPRequestHandler, directory=args.directory)
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as httpd:
        print(f"Serving {args.directory} at http://127.0.0.1:{args.port} (Range-capable). Ctrl-C.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")


if __name__ == "__main__":
    main()
