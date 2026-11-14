// DC Rooftop Solar Potential — static MapLibre + PMTiles map (Part 2-3, ADR-0010).
// Two layers (plan §5): a census-tract equity choropleth (inline GeoJSON, the default finding)
// and a 100k-roof layer (PMTiles vector tiles, z>=13 drill-down): suitable roofs on a viridis
// suitability ramp, non-suitable roofs dull grey with an outline (#40).

// --- PMTiles protocol so MapLibre can read pmtiles:// vector sources ---
const protocol = new pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

// --- palettes ---
const EQUITY_COLORS = {
  high_potential_high_burden: "#d7301f", // priority: high potential AND high burden
  high_potential_low_burden: "#238b45",
  low_potential_high_burden: "#6a51a3",
  low_potential_low_burden: "#bdbdbd",
  unknown: "#eeeeee",
};
const EQUITY_LABELS = {
  high_potential_high_burden: "High potential · high burden (priority)",
  high_potential_low_burden: "High potential · low burden",
  low_potential_high_burden: "Low potential · high burden",
  low_potential_low_burden: "Low potential · low burden",
  unknown: "No equity data",
};
const POTENTIAL_RAMP = ["#ffffcc", "#c2e699", "#78c679", "#31a354", "#006837"]; // YlGn
const BURDEN_RAMP = ["#fef0d9", "#fdcc8a", "#fc8d59", "#e34a33", "#b30000"]; // OrRd
const NO_DATA = "#eeeeee";
const SUITABILITY_RAMP = ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"]; // viridis, 0→100
const NOT_SUITABLE_FILL = "#b0b0b0";
const NOT_SUITABLE_OUTLINE = "#6e6e6e";

// Human labels for the pipeline's `unsuitable_reason` / `roof_class` values (#40).
const REASON_LABELS = {
  no_plane_fit: "No roof plane could be fitted",
  too_steep: "Too steep (> 45°)",
  north_facing: "North-facing",
  low_insolation: "Low sunlight (< 800 kWh/m²/yr)",
  too_small: "Too small (< 30 m² usable)",
};
const ROOF_CLASS_LABELS = {
  flat: "Flat",
  pitched_sun_facing: "Pitched, sun-facing",
  pitched_north_facing: "Pitched, north-facing",
};
const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];

// --- helpers ---
const fmtInt = (v) => Math.round(v).toLocaleString("en-US");
const fmtPct = (v) => (v * 100).toFixed(1) + "%";
const fmt1 = (v) => Number(v).toFixed(1);
const isNull = (v) => v == null || v === "" || Number.isNaN(Number(v));
// Format a possibly-null numeric property; null shows "—".
const orDash = (v, fmt, unit = "") => (isNull(v) ? "—" : fmt(v) + unit);
const compass = (deg) => COMPASS[Math.round((((deg % 360) + 360) % 360) / 45) % 8];

function percentile(sorted, p) {
  const i = (sorted.length - 1) * p;
  const lo = Math.floor(i);
  const hi = Math.ceil(i);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo);
}

// Quantile breaks robust to outliers (potential_per_household has a ~25M outlier vs ~12k median):
// 20/40/60/80 percentiles over the non-null values, deduped to a strictly-ascending set.
function quantileBins(values, ramp) {
  const sorted = values.filter((v) => v != null && isFinite(v)).sort((a, b) => a - b);
  const raw = [0.2, 0.4, 0.6, 0.8].map((p) => percentile(sorted, p));
  const breaks = [];
  for (const b of raw) if (breaks.length === 0 || b > breaks[breaks.length - 1]) breaks.push(b);
  const colors = ramp.slice(0, breaks.length + 1);
  return { breaks, colors };
}

// A MapLibre `step` paint expression over a numeric property, grey for null.
function stepExpr(prop, bins) {
  const step = ["step", ["get", prop], bins.colors[0]];
  bins.breaks.forEach((b, i) => step.push(b, bins.colors[i + 1]));
  return ["case", ["==", ["get", prop], null], NO_DATA, step];
}

const equityExpr = () => {
  const match = ["match", ["get", "equity_class"]];
  for (const [cls, color] of Object.entries(EQUITY_COLORS)) match.push(cls, color);
  match.push("#cccccc"); // default
  return match;
};

// --- map ---
const map = new maplibregl.Map({
  container: "map",
  style: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
  center: [-77.02, 38.9],
  zoom: 11,
  hash: true,
  attributionControl: {
    compact: true,
    customAttribution:
      "Roofs: MS Building Footprints · Tracts: US Census / DOE LEAD · Analysis: rooftop-solar-potential",
  },
});
map.addControl(new maplibregl.NavigationControl(), "top-left");
window.__map = map;

let tractData = null;
let bins = { potential: null, burden: null };

map.on("load", async () => {
  tractData = await (await fetch("./assets/tracts.geojson")).json();
  const props = tractData.features.map((f) => f.properties);
  bins.potential = quantileBins(props.map((p) => p.potential_per_household), POTENTIAL_RAMP);
  bins.burden = quantileBins(props.map((p) => p.energy_burden), BURDEN_RAMP);

  map.addSource("tracts", { type: "geojson", data: tractData });
  map.addLayer({
    id: "tract-fill",
    type: "fill",
    source: "tracts",
    paint: { "fill-color": equityExpr(), "fill-opacity": 0.65 },
  });
  map.addLayer({
    id: "tract-outline",
    type: "line",
    source: "tracts",
    paint: { "line-color": "#7a7a7a", "line-width": 0.5 },
  });
  map.addLayer({
    id: "tract-priority",
    type: "line",
    source: "tracts",
    filter: ["==", ["get", "is_priority"], true],
    paint: { "line-color": "#d7301f", "line-width": 1.8 },
  });

  // All ~100k roofs as vector tiles, z>=13 drill-down (plan §5). Suitable roofs get the
  // viridis ramp; the rest are grey + outlined (#40). `usable` is a boolean in the tiles, but
  // the String() match also tolerates "true" in case a pipeline hands it over as text.
  map.addSource("roofs", { type: "vector", url: "pmtiles://./assets/roofs.pmtiles" });
  const isUsable = ["==", ["to-string", ["get", "usable"]], "true"];
  map.addLayer({
    id: "roof-fill",
    type: "fill",
    source: "roofs",
    "source-layer": "roofs",
    minzoom: 13,
    filter: isUsable,
    paint: {
      "fill-color": [
        "interpolate", ["linear"], ["get", "suitability"],
        0, "#440154", 25, "#3b528b", 50, "#21918c", 75, "#5ec962", 100, "#fde725",
      ],
      "fill-opacity": 0.85,
    },
  });
  map.addLayer({
    id: "roof-unsuitable-fill",
    type: "fill",
    source: "roofs",
    "source-layer": "roofs",
    minzoom: 13,
    filter: ["!", isUsable],
    paint: { "fill-color": NOT_SUITABLE_FILL, "fill-opacity": 0.6 },
  });
  map.addLayer({
    id: "roof-unsuitable-outline",
    type: "line",
    source: "roofs",
    "source-layer": "roofs",
    minzoom: 13,
    filter: ["!", isUsable],
    paint: { "line-color": NOT_SUITABLE_OUTLINE, "line-width": 0.5 },
  });

  setLayer("equity");
  buildRoofLegend();
  wirePopups();
  window.__mapReady = true; // browser-smoke hook (plan §6)
});

// --- switcher ---
function setLayer(which) {
  if (which === "equity") map.setPaintProperty("tract-fill", "fill-color", equityExpr());
  else if (which === "potential")
    map.setPaintProperty("tract-fill", "fill-color", stepExpr("potential_per_household", bins.potential));
  else if (which === "burden")
    map.setPaintProperty("tract-fill", "fill-color", stepExpr("energy_burden", bins.burden));
  buildLegend(which);
  document.querySelectorAll("#switcher button").forEach((b) =>
    b.classList.toggle("active", b.dataset.layer === which)
  );
}

document.querySelectorAll("#switcher button").forEach((btn) =>
  btn.addEventListener("click", () => setLayer(btn.dataset.layer))
);

// --- legend ---
function row(color, label) {
  return `<div class="row"><span class="swatch" style="background:${color}"></span><span>${label}</span></div>`;
}

function rampRows(bins, fmt) {
  const { breaks, colors } = bins;
  const rows = [];
  colors.forEach((c, i) => {
    let label;
    if (i === 0) label = `< ${fmt(breaks[0])}`;
    else if (i === colors.length - 1) label = `≥ ${fmt(breaks[i - 1])}`;
    else label = `${fmt(breaks[i - 1])} – ${fmt(breaks[i])}`;
    rows.push(row(c, label));
  });
  return rows.join("");
}

// Roof legend: static, independent of the tract switcher (#40).
function buildRoofLegend() {
  const ramp = `linear-gradient(to right, ${SUITABILITY_RAMP.join(", ")})`;
  document.getElementById("roof-legend").innerHTML =
    `<div class="legend-title">Rooftop suitability</div>` +
    `<div class="ramp" style="background:${ramp}"></div>` +
    `<div class="ramp-labels"><span>0</span><span>100</span></div>` +
    `<div class="row"><span class="swatch" style="background:${NOT_SUITABLE_FILL};` +
    `border:1px solid ${NOT_SUITABLE_OUTLINE}"></span><span>Not suitable</span></div>`;
}

function buildLegend(which) {
  const el = document.getElementById("legend");
  if (which === "equity") {
    const rows = Object.keys(EQUITY_COLORS)
      .map((k) => row(EQUITY_COLORS[k], EQUITY_LABELS[k]))
      .join("");
    el.innerHTML =
      `<div class="legend-title">Equity quadrant</div>${rows}` +
      `<div class="priority-note">Priority tracts are outlined in red.</div>`;
  } else if (which === "potential") {
    el.innerHTML =
      `<div class="legend-title">Potential / household (kWh/yr)</div>` +
      rampRows(bins.potential, fmtInt) +
      row(NO_DATA, "No data");
  } else if (which === "burden") {
    el.innerHTML =
      `<div class="legend-title">Energy burden (% income on energy)</div>` +
      rampRows(bins.burden, fmtPct) +
      row(NO_DATA, "No data");
  }
}

// --- popups ---
function popupRow(k, v) {
  return `<div class="popup-row"><span class="k">${k}</span><span>${v}</span></div>`;
}

const ROOF_LAYERS = ["roof-fill", "roof-unsuitable-fill"];

function roofPopupHtml(p) {
  const suitable = String(p.usable) === "true";
  const status = suitable
    ? "Suitable"
    : "Not suitable — " + (REASON_LABELS[p.unsuitable_reason] || p.unsuitable_reason || "unknown");
  const aspect = isNull(p.aspect_deg) ? "—" : `${compass(p.aspect_deg)} (${fmtInt(p.aspect_deg)}°)`;
  const rows =
    popupRow("Status", status) +
    popupRow("Tilt", orDash(p.tilt_deg, fmt1, "°")) +
    popupRow("Aspect", aspect) +
    popupRow("Footprint area", orDash(p.footprint_area_m2, fmtInt, " m²")) +
    popupRow("Usable area", orDash(p.usable_area_m2, fmtInt, " m²")) +
    popupRow("Roof class", ROOF_CLASS_LABELS[p.roof_class] || "—") +
    popupRow("Real-sky POA", orDash(p.poa_real_kwh_m2, fmtInt, " kWh/m²/yr")) +
    popupRow("Planes", orDash(p.n_planes, fmtInt));
  const yields = suitable
    ? popupRow("Suitability", orDash(p.suitability, fmt1, " / 100")) +
      popupRow("Capacity", orDash(p.capacity_kw, fmt1, " kW")) +
      popupRow("Annual energy", orDash(p.annual_energy_kwh, fmtInt, " kWh/yr"))
    : "";
  const note =
    String(p.low_confidence) === "true"
      ? `<div class="popup-note">Low confidence: the roof-plane fit is poor.</div>`
      : "";
  return `<div class="popup-title">Rooftop</div>${rows}${yields}${note}`;
}

function wirePopups() {
  map.on("click", "tract-fill", (e) => {
    const p = e.features[0].properties;
    const pph = p.potential_per_household == null ? "—" : fmtInt(p.potential_per_household);
    const eb = p.energy_burden == null ? "—" : fmtPct(p.energy_burden);
    const priority =
      String(p.is_priority) === "true"
        ? `<div class="popup-priority">Priority tract</div>`
        : "";
    new maplibregl.Popup({ maxWidth: "260px" })
      .setLngLat(e.lngLat)
      .setHTML(
        `<div class="popup-title">Tract ${p.GEOID}</div>` +
          popupRow("Equity class", (EQUITY_LABELS[p.equity_class] || p.equity_class)) +
          popupRow("Potential / hh", pph + " kWh/yr") +
          popupRow("Energy burden", eb) +
          priority
      )
      .addTo(map);
  });

  for (const layer of ROOF_LAYERS) {
    map.on("click", layer, (e) => {
      new maplibregl.Popup({ maxWidth: "260px" })
        .setLngLat(e.lngLat)
        .setHTML(roofPopupHtml(e.features[0].properties))
        .addTo(map);
    });
  }

  for (const layer of ["tract-fill", ...ROOF_LAYERS]) {
    map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
  }
}

// --- about panel ---
document.getElementById("about-toggle").addEventListener("click", () => {
  document.getElementById("about").hidden = false;
});
document.getElementById("about-close").addEventListener("click", () => {
  document.getElementById("about").hidden = true;
});
