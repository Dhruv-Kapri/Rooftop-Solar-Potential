"""Browser smoke for the Glover Park walkthrough page (#41, ADR-0015); needs Playwright + Chromium.

Drives ``web/walkthrough.html`` over the same range-capable static server as the city-map smoke
(raster PMTiles are read by byte-range), steps through every stage with the Next button, and
asserts the page works: canvases render, raster tiles arrive as 206 range responses, the fitted
plane layer draws at step 5, and **no console errors** fire. Skipped until the walkthrough
assets have been built (``scripts/build_walkthrough.py``). Deselected by default; run with
``pytest -m integration``.
"""

from __future__ import annotations

import functools
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

# Reuse the city-map smoke's Range-capable handler (pytest puts tests/ on sys.path).
from test_web_smoke import RangeHTTPRequestHandler

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
MANIFEST = WEB_DIR / "assets" / "walkthrough" / "walkthrough.json"

pytest.importorskip("playwright.sync_api")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not MANIFEST.exists(), reason="walkthrough assets not built"),
]


@pytest.fixture
def base_url() -> Iterator[str]:
    handler = functools.partial(RangeHTTPRequestHandler, directory=str(WEB_DIR))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()


def test_walkthrough_steps_render_without_console_errors(base_url: str) -> None:
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    range_tile_responses: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1300, "height": 800})
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on(
            "response",
            lambda r: (
                range_tile_responses.append(r.url)
                if r.status == 206 and r.url.endswith(".pmtiles")
                else None
            ),
        )

        page.goto(f"{base_url}/walkthrough.html")
        page.wait_for_function("window.__walkReady === true", timeout=30000)
        assert page.locator("canvas.maplibregl-canvas").count() >= 2  # before + after maps

        n_steps = page.evaluate("window.__wt.steps.length")
        assert n_steps == 11
        for step in range(n_steps):
            assert page.evaluate("window.__wtStep") == step
            page.wait_for_timeout(500)  # let tiles for this step load
            if step == 4:  # step 5: fitted planes, coloured by aspect
                page.wait_for_function(
                    "window.__wt.after.queryRenderedFeatures({layers:['facets-kept']}).length > 0",
                    timeout=30000,
                )
                page.screenshot(path=str(WEB_DIR / "screenshot-walkthrough.png"))
            if step < n_steps - 1:
                page.click("#next")

        assert page.evaluate("window.__wtStep") == n_steps - 1
        assert range_tile_responses, "no raster PMTiles range requests (206) were made"
        browser.close()

    assert errors == [], f"console errors during walkthrough: {errors}"
