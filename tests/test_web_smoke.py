"""Browser smoke for the Part 2-3 web map (needs Playwright + Chromium) — plan §6, ADR-0010.

The honest ceiling for a mostly-visual deliverable: drive the real page in a headless browser
over a **range-capable** static server (PMTiles reads roof tiles by byte-range — stock
`http.server` can't, so this serves Range itself, matching GitHub Pages' CDN) and assert the map
actually works — the canvas renders, the **tract** layer draws at the default view, the **roof**
PMTiles layer draws once zoomed in (exercising range requests), and **no console errors** fire.
Captures committed screenshots as visual proof. This is NOT a per-PR e2e gate; the map's *look* is
manual. Deselected by default; run with `pytest -m integration` (needs `playwright install`).
"""

from __future__ import annotations

import functools
import http.server
import os
import re
import threading
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

# Skip cleanly if the browser stack isn't present (CI without playwright).
pytest.importorskip("playwright.sync_api")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (WEB_DIR / "index.html").exists(), reason="web/ not built"),
]


class RangeHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Static handler that honours a single ``Range: bytes=start-end`` request (206).

    Enough for PMTiles' byte-range reads; stock ``SimpleHTTPRequestHandler`` ignores Range and
    returns the whole file (200), which breaks the tiled roof layer.
    """

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

    def log_message(self, *args):  # silence per-request logging
        pass


@pytest.fixture
def base_url():
    handler = functools.partial(RangeHTTPRequestHandler, directory=str(WEB_DIR))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()


def test_map_renders_both_layers_without_console_errors(base_url):
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        page.goto(f"{base_url}/index.html")
        page.wait_for_function("window.__mapReady === true", timeout=30000)

        # The map canvas renders.
        assert page.locator("canvas.maplibregl-canvas").count() >= 1

        # The tract equity layer draws at the default view (the headline choropleth).
        n_tracts = page.evaluate(
            "window.__map.queryRenderedFeatures({layers:['tract-fill']}).length"
        )
        assert n_tracts > 0, "no tract features rendered"
        page.wait_for_timeout(400)
        page.screenshot(path=str(WEB_DIR / "screenshot.png"))

        # Zoom into a dense downtown block so the roof PMTiles layer (z>=13) loads via range
        # requests, then draws.
        page.evaluate("window.__map.jumpTo({center:[-77.036,38.905], zoom:15})")
        page.wait_for_function(
            "window.__map.isSourceLoaded('roofs') && "
            "window.__map.queryRenderedFeatures({layers:['roof-fill']}).length > 0",
            timeout=30000,
        )
        n_roofs = page.evaluate(
            "window.__map.queryRenderedFeatures({layers:['roof-fill']}).length"
        )
        assert n_roofs > 0, "no roof features rendered from PMTiles"
        page.wait_for_timeout(400)
        page.screenshot(path=str(WEB_DIR / "screenshot-roofs.png"))

        browser.close()

    assert errors == [], f"console errors during load: {errors}"
