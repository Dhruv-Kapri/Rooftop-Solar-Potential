// DC Rooftop Solar Potential — static MapLibre + PMTiles map (Part 2-3, ADR-0010).
// Two layers (plan §5): a census-tract equity choropleth (inline GeoJSON, the default finding)
// and a 100k-roof suitability layer (PMTiles vector tiles, z>=13 drill-down).

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

// --- helpers ---
const fmtInt = (v) => Math.round(v).toLocaleString("en-US");
const fmtPct = (v) => (v * 100).toFixed(1) + "%";
const fmt1 = (v) => Number(v).toFixed(1);

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

  // 100k usable roofs as vector tiles, z>=13 drill-down (plan §5).
  map.addSource("roofs", { type: "vector", url: "pmtiles://./assets/roofs.pmtiles" });
  map.addLayer({
    id: "roof-fill",
    type: "fill",
    source: "roofs",
    "source-layer": "roofs",
    minzoom: 13,
    paint: {
      "fill-color": [
        "interpolate", ["linear"], ["get", "suitability"],
        0, "#440154", 25, "#3b528b", 50, "#21918c", 75, "#5ec962", 100, "#fde725",
      ],
      "fill-opacity": 0.85,
    },
  });

  setLayer("equity");
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

  map.on("click", "roof-fill", (e) => {
    const p = e.features[0].properties;
    new maplibregl.Popup({ maxWidth: "240px" })
      .setLngLat(e.lngLat)
      .setHTML(
        `<div class="popup-title">Rooftop</div>` +
          popupRow("Suitability", fmt1(p.suitability) + " / 100") +
          popupRow("Capacity", fmt1(p.capacity_kw) + " kW") +
          popupRow("Annual energy", fmtInt(p.annual_energy_kwh) + " kWh/yr")
      )
      .addTo(map);
  });

  for (const layer of ["tract-fill", "roof-fill"]) {
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
