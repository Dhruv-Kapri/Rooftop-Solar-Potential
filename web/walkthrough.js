// Glover Park walkthrough (#41, ADR-0015): a stepper over two synced MapLibre maps.
// Left map = the previous step, right map = the current step, joined by maplibre-gl-compare.
// Assets are read relative to this page (GitHub Pages serves under a subpath).

const ASSET_DIR = "./assets/walkthrough/";
const BASEMAP = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";
const ESRI_URL = "https://learn.arcgis.com/en/projects/estimate-solar-power-potential/";
const ESRI_GATE = 0.15; // ADR-0005: specific yield within +/-15% of Esri's
const FLAT_TILT_DEG = 10; // roof_planes flat cutoff; flat facets have no meaningful aspect
const FLAT_COLOR = "#bebebe"; // = walkthrough._FLAT_RGBA, the aspect raster's flat grey
const REMOVED_COLOR = "#9e9e9e";
const NOFIT_COLOR = "#bdbdbd";
const OBSTRUCTION_COLOR = "#d7301f";
const ENERGY_RAMP = ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"]; // YlOrRd

const protocol = new pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

// --- helpers ---
const fmtInt = (v) => Math.round(v).toLocaleString("en-US");
const fmt1 = (v) => Number(v).toFixed(1);
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const compass = (deg) => `${COMPASS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16]} ${Math.round(deg)}°`;
const assetUrl = (name) => new URL(ASSET_DIR + name, window.location.href).href;

function row(color, label, cls = "") {
  return `<div class="row"><span class="swatch ${cls}" style="background:${color}"></span><span>${label}</span></div>`;
}
function rampBar(stops, min, max, unit) {
  const span = max - min || 1;
  const css = stops.map(([v, c]) => `${c} ${(((v - min) / span) * 100).toFixed(1)}%`).join(", ");
  return (
    `<div class="ramp" style="background:linear-gradient(to right, ${css})"></div>` +
    `<div class="ramp-labels"><span>${fmtInt(min)}</span><span>${unit}</span><span>${fmtInt(max)}</span></div>`
  );
}
const percentile = (sorted, p) => sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * p))];

// --- steps (plan §3). `state` drives layer visibility; see applyState. ---
// raster: overlay raster name; removed: reasons struck out so far; just: reasons struck at this step.
const STEPS = [
  {
    id: "1", label: "1", title: "1 · Surface model (DSM) hillshade",
    state: { raster: "hillshade" },
    body: [
      "We start from a 2 m LiDAR digital <em>surface</em> model: the top of everything, buildings and trees included. Hillshading just makes the heights readable.",
      "It has to be the surface model, not bare earth. Only the surface model lets one building shade another.",
    ],
  },
  {
    id: "2", label: "2", title: "2 · Solar insolation (with shading)",
    state: { raster: "insolation", under: true },
    body: [
      "GRASS <code>r.sun</code> sums sunlight over a year at every pixel, including shadows cast by neighbouring buildings. Shown here only over roofs.",
      "This is the same quantity as the tutorial's Solar_Rad raster (we sample 12 mid-month days; the tutorial integrates the full year).",
    ],
  },
  {
    id: "3", label: "3", title: "3 · Pixel slope",
    state: { raster: "slope", under: true },
    body: [
      "Slope of the surface model, per pixel, inside building footprints. This matches the tutorial's Slope_DSM step.",
      "<span class='note'>Context only: our cutoffs act on fitted roof planes (step 5 onward), not on individual pixels, because pixel slope is noisy.</span>",
    ],
  },
  {
    id: "4", label: "4", title: "4 · Pixel aspect",
    state: { raster: "aspect", under: true },
    body: [
      "Compass direction each pixel faces (0° = north, clockwise). Roofs under 10° tilt are shown as flat, since they have no meaningful direction.",
      "<span class='note'>Also context for tutorial parity. The pipeline decides on plane aspect, not pixel aspect.</span>",
    ],
  },
  {
    id: "5", label: "5", title: "5 · Fitted roof planes",
    state: { facets: "aspect", outline: true },
    body: [
      "Where the tutorial keeps pixels, we group pixels into <em>planes</em>: a sequential RANSAC fit finds each roof's facets (a gable becomes two). Each is coloured by the direction it faces.",
      "Buildings where no plane could be fitted stay grey and are excluded from here on.",
    ],
    stats: (m) => `${fmtInt(m.stats.n_planes)} planes fitted on ${fmtInt(m.stats.n_buildings)} buildings; ${fmtInt(m.stats.reason_counts.no_plane_fit || 0)} with no fit`,
  },
  {
    id: "6", label: "6", title: "6 · Obstructions removed",
    state: { facets: "aspect", outline: true, obstructions: true },
    body: [
      "Chimneys, HVAC units and stair towers are detected as small raised blobs on a plane and cut out of the usable area (red).",
      "<span class='note'>The tutorial has no equivalent step. This one is ours.</span>",
    ],
  },
  {
    id: "7a", label: "7a", title: "7a · Remove: too steep",
    state: { facets: "aspect", outline: true, obstructions: true, removed: ["too_steep"], just: ["too_steep"] },
    reason: "too_steep",
    body: [
      "Planes tilted more than 45° can't hold panels well. The tutorial applies the same slope cutoff to pixels.",
      "Cutoffs now run one at a time. Removed planes turn grey; the ones just removed are outlined.",
    ],
  },
  {
    id: "7b", label: "7b", title: "7b · Remove: north-facing",
    state: { facets: "aspect", outline: true, obstructions: true, removed: ["too_steep", "north_facing"], just: ["north_facing"] },
    reason: "north_facing",
    body: [
      "Tilted planes that face north get little direct sun in the northern hemisphere. Flat roofs are kept, since panels there are racked to face south.",
      "<span class='note'>We run this before the insolation cutoff, in the pipeline's order: a plane failing several cutoffs is labelled by the first.</span>",
    ],
  },
  {
    id: "7c", label: "7c", title: "7c · Remove: low insolation",
    state: { facets: "aspect", outline: true, obstructions: true, removed: ["too_steep", "north_facing", "low_insolation"], just: ["low_insolation"] },
    reason: "low_insolation",
    body: [
      "Planes receiving under 800 kWh/m² a year (heavily shaded, mostly) are removed. This is the tutorial's radiation cutoff.",
    ],
  },
  {
    id: "7d", label: "7d", title: "7d · Remove: too small",
    state: { facets: "aspect", outline: true, obstructions: true, removed: ["too_steep", "north_facing", "low_insolation", "too_small"], just: ["too_small"], tooSmall: true },
    reason: "too_small",
    body: [
      "Finally, roofs with under 30 m² of usable area are dropped, as in the tutorial's Suitable_Buildings step. A plane is also removed when its whole building falls short.",
    ],
  },
  {
    id: "8", label: "8", title: "8 · Suitable buildings: annual energy",
    state: { suitable: true, outline: true },
    body: [
      "What survives is turned into capacity and annual energy (PVWatts, fed the shaded insolation), then summed per building. Darker means more MWh a year.",
      `Compare with the tutorial's Elec_Prod_MWh: <a href="${ESRI_URL}" target="_blank" rel="noopener">Esri's Estimate solar power potential</a>.`,
    ],
    stats: (m) => `${fmtInt(m.stats.n_suitable)} suitable of ${fmtInt(m.stats.n_buildings)} buildings; ${fmtInt(m.stats.total_energy_mwh)} MWh/yr in total`,
  },
];
const NO_STATE = {}; // the left map at step 1: basemap only

// --- layers ---
const RASTER_NAMES = ["hillshade", "insolation", "slope", "aspect"];
const LAYER_IDS = [
  ...RASTER_NAMES.map((n) => `raster-${n}`),
  "buildings-nofit", "buildings-suitable", "facets-removed", "facets-removed-line",
  "facets-kept", "facets-just", "obstructions", "buildings-outline",
];

function aspectColorExpr(manifest) {
  const stops = manifest.rasters.aspect.legend.stops;
  const interp = ["interpolate", ["linear"], ["get", "aspect_deg"]];
  stops.forEach(([v, c]) => interp.push(v, c));
  return ["case", ["<=", ["get", "tilt_deg"], FLAT_TILT_DEG], FLAT_COLOR, interp];
}

function energyColorExpr(maxMwh) {
  const step = maxMwh / (ENERGY_RAMP.length - 1);
  const interp = ["interpolate", ["linear"], ["get", "annual_energy_mwh"]];
  ENERGY_RAMP.forEach((c, i) => interp.push(i * step, c));
  return interp;
}

function addLayers(map, ctx) {
  const { manifest, data } = ctx;
  for (const name of RASTER_NAMES) {
    map.addSource(`src-${name}`, { type: "raster", url: "pmtiles://" + assetUrl(manifest.rasters[name].url), tileSize: 256 });
    map.addLayer({ id: `raster-${name}`, type: "raster", source: `src-${name}`, layout: { visibility: "none" } });
  }
  for (const k of ["facets", "obstructions", "buildings"]) {
    map.addSource(`src-${k}`, { type: "geojson", data: data[k] });
  }
  const hidden = { visibility: "none" };
  map.addLayer({
    id: "buildings-nofit", type: "fill", source: "src-buildings", layout: hidden,
    filter: ["==", ["get", "unsuitable_reason"], "no_plane_fit"],
    paint: { "fill-color": NOFIT_COLOR, "fill-opacity": 0.8 },
  });
  map.addLayer({
    id: "buildings-suitable", type: "fill", source: "src-buildings", layout: hidden,
    filter: ["==", ["get", "usable"], true],
    paint: { "fill-color": energyColorExpr(ctx.maxMwh), "fill-opacity": 0.9 },
  });
  map.addLayer({ id: "facets-removed", type: "fill", source: "src-facets", layout: hidden, paint: { "fill-color": REMOVED_COLOR, "fill-opacity": 0.35 } });
  map.addLayer({ id: "facets-removed-line", type: "line", source: "src-facets", layout: hidden, paint: { "line-color": "#616161", "line-width": 0.5 } });
  map.addLayer({ id: "facets-kept", type: "fill", source: "src-facets", layout: hidden, paint: { "fill-color": aspectColorExpr(manifest), "fill-opacity": 0.85 } });
  map.addLayer({ id: "facets-just", type: "line", source: "src-facets", layout: hidden, paint: { "line-color": "#212121", "line-width": 2 } });
  map.addLayer({ id: "obstructions", type: "fill", source: "src-obstructions", layout: hidden, paint: { "fill-color": OBSTRUCTION_COLOR, "fill-opacity": 0.9 } });
  map.addLayer({ id: "buildings-outline", type: "line", source: "src-buildings", layout: hidden, paint: { "line-color": "#333", "line-width": 1 } });
}

// A facet is struck out when its own reason is in `reasons`; at 7d, also when its building is too small.
function reasonFilter(reasons, tooSmallBuilding) {
  const own = ["in", ["coalesce", ["get", "reason"], ""], ["literal", reasons]];
  if (!tooSmallBuilding) return own;
  return ["any", own, ["==", ["coalesce", ["get", "building_reason"], ""], "too_small"]];
}

function applyState(map, st) {
  const vis = {};
  for (const id of LAYER_IDS) vis[id] = false;
  if (st.raster) {
    vis["raster-hillshade"] = true;
    vis[`raster-${st.raster}`] = true;
  }
  const hasFacets = !!st.facets;
  const removed = st.removed || [];
  if (hasFacets) {
    vis["buildings-nofit"] = true;
    vis["facets-kept"] = true;
    vis["facets-removed"] = removed.length > 0;
    vis["facets-removed-line"] = removed.length > 0;
    vis["facets-just"] = !!st.just;
    vis.obstructions = !!st.obstructions;
  }
  if (st.suitable) {
    vis["buildings-nofit"] = true;
    vis["buildings-suitable"] = true;
  }
  vis["buildings-outline"] = !!st.outline;
  for (const [id, v] of Object.entries(vis)) map.setLayoutProperty(id, "visibility", v ? "visible" : "none");

  // The overlay raster sits on a dimmed hillshade (steps 2-4); alone, the hillshade is full strength.
  map.setPaintProperty("raster-hillshade", "raster-opacity", st.under ? 0.7 : 0.95);
  for (const n of ["insolation", "slope", "aspect"]) map.setPaintProperty(`raster-${n}`, "raster-opacity", 0.85);

  if (hasFacets) {
    const rem = reasonFilter(removed, st.tooSmall);
    map.setFilter("facets-removed", rem);
    map.setFilter("facets-removed-line", rem);
    map.setFilter("facets-kept", ["!", rem]);
    map.setFilter("facets-just", reasonFilter(st.just || [], st.tooSmall && (st.just || []).includes("too_small")));
  }
}

// --- popups ---
function popupRow(k, v) {
  return `<div class="popup-row"><span class="k">${k}</span><span>${v}</span></div>`;
}
const REASON_TEXT = {
  too_steep: "Removed: too steep", low_insolation: "Removed: low insolation", north_facing: "Removed: north-facing",
  too_small: "Removed: too small", no_plane_fit: "No plane fitted",
};

function facetPopup(p) {
  const status = p.reason ? REASON_TEXT[p.reason] || p.reason : p.building_reason === "too_small" ? REASON_TEXT.too_small + " (building)" : "Passes the plane cutoffs";
  return (
    `<div class="popup-title">Roof plane</div>` +
    popupRow("Tilt", fmt1(p.tilt_deg) + "°") +
    popupRow("Aspect", compass(p.aspect_deg)) +
    popupRow("Area", fmt1(p.area_m2) + " m²") +
    popupRow("Insolation", fmtInt(p.poa_kwh_m2) + " kWh/m²/yr") +
    popupRow("Status", status)
  );
}
function buildingPopup(p) {
  const usable = p.usable === true || p.usable === "true";
  const head = `<div class="popup-title">Building</div>`;
  if (!usable) return head + popupRow("Status", REASON_TEXT[p.unsuitable_reason] || p.unsuitable_reason || "Not suitable");
  return (
    head +
    popupRow("Annual energy", fmt1(p.annual_energy_mwh) + " MWh/yr") +
    popupRow("Capacity", fmt1(p.capacity_kw) + " kW") +
    popupRow("Usable area", fmt1(p.usable_area_m2) + " m²") +
    popupRow("Planes", String(p.n_planes))
  );
}
function obstructionPopup(p) {
  return `<div class="popup-title">Obstruction</div>` + popupRow("Area", fmt1(p.area_m2) + " m²");
}

function wirePopups(map) {
  const targets = [
    ["obstructions", obstructionPopup],
    ["facets-kept", facetPopup],
    ["facets-removed", facetPopup],
    ["buildings-suitable", buildingPopup],
    ["buildings-nofit", buildingPopup],
  ];
  map.on("click", (e) => {
    const layers = targets.filter(([id]) => map.getLayoutProperty(id, "visibility") === "visible").map(([id]) => id);
    if (!layers.length) return;
    const hit = map.queryRenderedFeatures(e.point, { layers })[0]; // first = topmost in `layers` order of the style
    if (!hit) return;
    const render = targets.find(([id]) => id === hit.layer.id)[1];
    new maplibregl.Popup({ maxWidth: "240px" }).setLngLat(e.lngLat).setHTML(render(hit.properties)).addTo(map);
  });
  for (const [id] of targets) {
    map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
  }
}

// --- legend / stats / benchmark ---
function rasterLegend(manifest, name, title) {
  const lg = manifest.rasters[name].legend;
  const extra = name === "aspect" ? row(FLAT_COLOR, `Flat (under ${FLAT_TILT_DEG}° tilt)`) : "";
  return `<div class="legend-title">${title}</div>` + rampBar(lg.stops, lg.min, lg.max, lg.unit) + extra;
}

function aspectLegend(manifest) {
  const lg = manifest.rasters.aspect.legend;
  return (
    `<div class="legend-title">Plane aspect</div>` +
    rampBar(lg.stops, lg.min, lg.max, lg.unit) +
    row(FLAT_COLOR, `Flat (under ${FLAT_TILT_DEG}° tilt)`)
  );
}

function legendHtml(step, ctx) {
  const m = ctx.manifest;
  const s = step.state;
  if (s.raster === "hillshade") return `<div class="legend-title">Surface height, shaded relief</div>` + row("#555", "Darker = shadowed side; brighter = sun-facing");
  if (s.raster === "insolation") return rasterLegend(m, "insolation", "Annual insolation");
  if (s.raster === "slope") return rasterLegend(m, "slope", "Pixel slope");
  if (s.raster === "aspect") return rasterLegend(m, "aspect", "Pixel aspect");
  if (s.suitable) {
    const css = ENERGY_RAMP.map((c, i) => `${c} ${((i / (ENERGY_RAMP.length - 1)) * 100).toFixed(0)}%`).join(", ");
    return (
      `<div class="legend-title">Annual energy per building</div>` +
      `<div class="ramp" style="background:linear-gradient(to right, ${css})"></div>` +
      `<div class="ramp-labels"><span>0</span><span>MWh/yr</span><span>${fmtInt(ctx.maxMwh)}+</span></div>` +
      row(NOFIT_COLOR, "Not suitable or no plane fitted")
    );
  }
  let html = aspectLegend(m) + row(NOFIT_COLOR, "Building with no fitted plane");
  if (s.obstructions) html += row(OBSTRUCTION_COLOR, "Obstruction");
  if (s.removed && s.removed.length) {
    html += row(REMOVED_COLOR, "Removed by an earlier or this cutoff");
    html += row("transparent", "Just removed at this step", "outline");
  }
  return html;
}

function statsText(step, ctx) {
  if (step.stats) return step.stats(ctx.manifest);
  if (step.reason) {
    const n = ctx.manifest.stats.reason_counts[step.reason] || 0;
    return `${fmtInt(n)} removed at this step`;
  }
  return "";
}

function benchmarkHtml(m) {
  const s = m.stats;
  const delta = s.specific_yield_kwh_kwp / s.esri_specific_yield_kwh_kwp - 1;
  const pass = Math.abs(delta) <= ESRI_GATE;
  return {
    pass,
    html:
      `<strong>Benchmark (ADR-0005)</strong>` +
      popupRow("Our specific yield", fmtInt(s.specific_yield_kwh_kwp) + " kWh/kWp") +
      popupRow("Esri tutorial", fmtInt(s.esri_specific_yield_kwh_kwp) + " kWh/kWp") +
      popupRow("Difference", `${delta >= 0 ? "+" : ""}${(delta * 100).toFixed(1)}% (gate ±${ESRI_GATE * 100}%) ${pass ? "· within gate" : "· outside gate"}`) +
      popupRow("Median per building", `${fmt1(s.median_energy_mwh)} vs Esri ${fmt1(s.esri_median_energy_mwh)} MWh`),
  };
}

// --- stepper ---
let ctx = null;
let current = 0;

function render() {
  const step = STEPS[current];
  const prev = current === 0 ? NO_STATE : STEPS[current - 1].state;
  applyState(ctx.before, prev);
  applyState(ctx.after, step.state);

  document.getElementById("tag-before").textContent = current === 0 ? "Basemap" : `Step ${STEPS[current - 1].label}`;
  document.getElementById("tag-after").textContent = `Step ${step.label}`;
  document.getElementById("step-title").textContent = step.title;
  document.getElementById("step-body").innerHTML = step.body.map((t) => `<p>${t}</p>`).join("");
  document.getElementById("step-stats").textContent = statsText(step, ctx);
  document.getElementById("legend").innerHTML = legendHtml(step, ctx);
  document.getElementById("counter").textContent = `${current + 1} / ${STEPS.length}`;
  document.getElementById("prev").disabled = current === 0;
  document.getElementById("next").disabled = current === STEPS.length - 1;

  const bench = document.getElementById("benchmark");
  bench.hidden = !step.state.suitable;
  if (step.state.suitable) {
    const b = benchmarkHtml(ctx.manifest);
    bench.innerHTML = b.html;
    bench.classList.toggle("fail", !b.pass);
  }
  document.querySelectorAll("#steps button").forEach((btn, i) => {
    btn.classList.toggle("active", i === current);
    btn.classList.toggle("done", i < current);
  });
  window.__wtStep = current;
}

function goTo(i) {
  current = Math.max(0, Math.min(STEPS.length - 1, i));
  render();
}

function buildStepList() {
  const ol = document.getElementById("steps");
  STEPS.forEach((s, i) => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = s.label;
    b.title = s.title;
    b.addEventListener("click", () => goTo(i));
    li.appendChild(b);
    ol.appendChild(li);
  });
  document.getElementById("prev").addEventListener("click", () => goTo(current - 1));
  document.getElementById("next").addEventListener("click", () => goTo(current + 1));
  document.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") goTo(current - 1);
    else if (e.key === "ArrowRight") goTo(current + 1);
  });
}

function newMap(container, manifest) {
  return new maplibregl.Map({
    container,
    style: BASEMAP,
    center: manifest.center,
    zoom: 15,
    attributionControl: container === "after" ? { compact: true, customAttribution: "Glover Park walkthrough · rooftop-solar-potential" } : false,
  });
}

async function main() {
  const fetchJson = async (name) => (await fetch(assetUrl(name))).json();
  const manifest = await fetchJson("walkthrough.json");
  const [facets, obstructions, buildings] = await Promise.all([
    fetchJson(manifest.vectors.facets), fetchJson(manifest.vectors.obstructions), fetchJson(manifest.vectors.buildings),
  ]);
  const energies = buildings.features
    .filter((f) => f.properties.usable === true && f.properties.annual_energy_mwh != null)
    .map((f) => f.properties.annual_energy_mwh)
    .sort((a, b) => a - b);
  const maxMwh = energies.length ? Math.max(percentile(energies, 0.95), 1e-6) : 1; // p95 keeps one giant roof from washing out the ramp

  const before = newMap("before", manifest);
  const after = newMap("after", manifest);
  before.addControl(new maplibregl.NavigationControl(), "top-right");
  new maplibregl.Compare(before, after, "#comparison", { orientation: "vertical" });

  ctx = { manifest, data: { facets, obstructions, buildings }, maxMwh, before, after };
  buildStepList();

  await Promise.all([before, after].map((m) => new Promise((res) => m.on("load", res))));
  for (const m of [before, after]) {
    addLayers(m, ctx);
    wirePopups(m);
    m.fitBounds([[manifest.bounds[0], manifest.bounds[1]], [manifest.bounds[2], manifest.bounds[3]]], { padding: { top: 20, bottom: 20, left: 400, right: 20 }, duration: 0 });
  }
  render();
  window.__wt = { before, after, goTo, steps: STEPS.map((s) => s.id) }; // browser-smoke hooks
  window.__walkReady = true;
}

main().catch((err) => {
  console.error(err);
  document.getElementById("step-title").textContent = "Walkthrough assets not found";
  document.getElementById("step-body").innerHTML = "<p>Build them with <code>scripts/build_walkthrough.py</code>.</p>";
});
