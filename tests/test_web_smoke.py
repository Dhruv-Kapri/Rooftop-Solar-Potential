"""Browser smoke for the Part 2-3 web map (needs Playwright + Chromium) — plan §6, ADR-0010.

The honest ceiling for a mostly-visual deliverable: drive the real page in a headless browser
over a **range-capable** static server (PMTiles reads roof tiles by byte-range — stock
`http.server` can't, so this serves Range itself, matching GitHub Pages' CDN) and assert the map
actually works — the canvas renders, the **tract** layer draws at the default view, both **roof**
layers (suitable ramp + grey non-suitable, #40) draw once zoomed in (exercising range requests),
the roof legend renders, and **no console errors** fire. The roof PMTiles is a small synthetic
fixture built per run (the committed `web/assets/roofs.pmtiles` may predate the #40 schema); the
tract screenshot is committed as visual proof, the roof one is left alone. This is NOT a per-PR
e2e gate; the map's *look* is manual. Deselected by default; run with `pytest -m integration`
(needs `playwright install`).
"""

from __future__ import annotations

import functools
import http.server
import os
import re
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path

import geopandas as gpd
import pytest
from pyproj import Transformer
from shapely.geometry import box

from rooftop_solar import config, web_build

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

# Skip cleanly if the browser stack isn't present (CI without playwright).
pytest.importorskip("playwright.sync_api")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (WEB_DIR / "index.html").exists(), reason="web/ not built"),
    pytest.mark.skipif(shutil.which("tippecanoe") is None, reason="tippecanoe not on PATH"),
]

_VIEW_LON, _VIEW_LAT = -77.036, 38.905


def _fixture_roofs() -> gpd.GeoDataFrame:
    """A 6x6 grid of 12 m roofs at the test view, alternating suitable / too_small."""
    cx, cy = Transformer.from_crs("EPSG:4326", config.WORKING_CRS, always_xy=True).transform(
        _VIEW_LON, _VIEW_LAT
    )
    cells = [(i, j) for i in range(6) for j in range(6)]
    usable = [(i + j) % 2 == 0 for i, j in cells]
    n = len(cells)
    return gpd.GeoDataFrame(
        {
            "usable": usable,
            "unsuitable_reason": [None if u else "too_small" for u in usable],
            "suitability": [70.0 if u else 0.0 for u in usable],
            "capacity_kw": [5.0 if u else 0.0 for u in usable],
            "annual_energy_kwh": [6000.0 if u else 0.0 for u in usable],
            "tilt_deg": [20.0] * n,
            "aspect_deg": [180.0] * n,
            "footprint_area_m2": [144.0] * n,
            "usable_area_m2": [90.0 if u else 10.0 for u in usable],
            "roof_class": ["flat"] * n,
            "poa_real_kwh_m2": [1300.0] * n,
            "n_planes": [1] * n,
            "low_confidence": [False] * n,
        },
        geometry=[
            box(cx + i * 15 - 45, cy + j * 15 - 45, cx + i * 15 - 33, cy + j * 15 - 33)
            for i, j in cells
        ],
        crs=config.WORKING_CRS,
    )


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
def base_url(tmp_path: Path) -> Iterator[str]:
    # Serve a copy of web/ whose roof tiles are the synthetic fixture (see module docstring).
    site = tmp_path / "web"
    shutil.copytree(WEB_DIR, site, ignore=shutil.ignore_patterns("*.png"))
    shutil.copy(WEB_DIR / "assets" / "scatter.png", site / "assets" / "scatter.png")
    web_build.write_roof_pmtiles(_fixture_roofs(), site / "assets" / "roofs.pmtiles")
    handler = functools.partial(RangeHTTPRequestHandler, directory=str(site))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()


def test_map_renders_both_layers_without_console_errors(base_url: str) -> None:
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

        # The roof legend renders with its "Not suitable" row; both roof layers exist (#40).
        legend = page.locator("#roof-legend").inner_text()
        assert "Rooftop suitability" in legend and "Not suitable" in legend
        for layer in ("roof-fill", "roof-unsuitable-fill", "roof-unsuitable-outline"):
            assert page.evaluate(f"!!window.__map.getLayer('{layer}')"), layer

        # Zoom onto the fixture roofs so the PMTiles layer (z>=13) loads via range requests,
        # then both the suitable and the non-suitable fill draw.
        page.evaluate(f"window.__map.jumpTo({{center:[{_VIEW_LON},{_VIEW_LAT}], zoom:18}})")
        count = "window.__map.queryRenderedFeatures({layers:['%s']}).length"
        page.wait_for_function(
            "window.__map.isSourceLoaded('roofs') && "
            + (count % "roof-fill")
            + " > 0 && "
            + (count % "roof-unsuitable-fill")
            + " > 0",
            timeout=30000,
        )
        page.wait_for_timeout(400)

        # A click on a non-suitable roof opens the shared popup with the reason label.
        pt = page.evaluate(
            """() => {
                const f = window.__map.queryRenderedFeatures({layers:['roof-unsuitable-fill']})[0];
                const xs = f.geometry.coordinates[0].map(p => p[0]);
                const ys = f.geometry.coordinates[0].map(p => p[1]);
                const mid = [
                  (Math.min(...xs) + Math.max(...xs)) / 2,
                  (Math.min(...ys) + Math.max(...ys)) / 2,
                ];
                const px = window.__map.project(mid);
                return [px.x, px.y];
            }"""
        )
        page.mouse.click(pt[0], pt[1])
        # (the tract popup under the roof opens too — pick the roof one)
        roof_popup = page.locator(".maplibregl-popup", has_text="Rooftop")
        roof_popup.wait_for()
        popup = roof_popup.inner_text()
        assert "Not suitable" in popup and "Too small" in popup

        browser.close()

    assert errors == [], f"console errors during load: {errors}"
