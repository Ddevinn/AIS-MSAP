const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const elements = new Map();
const element = (id) => {
  if (!elements.has(id)) elements.set(id, {
    hidden: false, textContent: "", innerHTML: "", style: {},
    selectedOptions: [{ text: "转向" }], setAttribute(name, value) { this[name] = value; },
  });
  return elements.get(id);
};
const sources = new Map();
const layers = new Map([["marker-region-fill", {}], ["ais-tracks-raster", {}], ["osm", {}]]);
const map = {
  addSource(id, options) { sources.set(id, { data: options.data, setData(data) { this.data = data; } }); },
  addLayer(layer) { layers.set(layer.id, { ...layer, layout: {} }); },
  getLayer: (id) => layers.get(id), getSource: (id) => sources.get(id),
  setLayoutProperty(id, name, value) { (layers.get(id).layout ||= {})[name] = value; },
  setPaintProperty(id, name, value) { (layers.get(id).paint ||= {})[name] = value; },
  on() {}, getCanvas: () => ({ addEventListener() {} }),
};
const pending = [];
const context = vm.createContext({
  window: { addEventListener() {} }, AbortController, Math, console,
  document: { querySelectorAll: () => [], querySelector: () => element("colorbar") },
  $: element, state: { map, mapLoaded: true, manifest: {}, activeKey: "dataset-1", filters: {}, behaviorFilter: null },
  numberFormat: new Intl.NumberFormat("zh-CN"), formatNumber: (value) => String(value),
  queryString(extra) { return "?" + new URLSearchParams({ ...context.state.filters, ...extra }); },
  fetch(url) { return new Promise((resolve, reject) => pending.push({ url, resolve, reject })); },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/density.js"), "utf8"), context);
const data = (count) => ({ type: "FeatureCollection", metadata: {
  max_count: count, point_count: count, cell_count: 1, cell_size_m: 250,
}, features: [{ type: "Feature", geometry: { type: "Polygon", coordinates: [] },
  properties: { point_count: count, vessel_count: 1, center_lon: 110, center_lat: 20 } }] });
const respond = (request, count) => request.resolve({ ok: true, json: async () => data(count) });

(async () => {
  context.initDensityLayers();
  assert.equal(layers.get("ais-density-heat").layout.visibility, "visible");
  assert.equal(layers.get("ais-density-grid").layout.visibility, "none");
  const oldRequest = context.refreshDensityLayer();
  context.state.filters.mmsi = 413000002;
  const latestRequest = context.refreshDensityLayer();
  respond(pending[1], 12);
  await latestRequest;
  respond(pending[0], 500);
  await oldRequest;
  assert.equal(element("densityPointCount").textContent, "12", "Late data must not replace the latest filter");
  assert.equal(sources.get("ais-density-cells").data.features[0].properties.point_count, 12);
  assert.equal(sources.get("ais-density-points").data.features[0].properties.heat_weight, 1);

  vm.runInContext('densityState.mode = "grid"; densityState.showTracks = false; updateDensityStyle();', context);
  assert.equal(layers.get("ais-density-grid").layout.visibility, "visible");
  assert.equal(layers.get("ais-density-heat").layout.visibility, "none");
  assert.equal(layers.get("ais-tracks-raster").layout.visibility, "none");
  assert.equal(element("densityToggleBtn")["aria-pressed"], "true");

  context.state.filters.mmsi = 413000003;
  const failure = context.refreshDensityLayer();
  pending[2].reject(new Error("offline"));
  await failure;
  assert.equal(element("densityLegendBody").hidden, true, "Failed request must not show stale totals");
  assert.equal(sources.get("ais-density-cells").data.features.length, 0);
  const retry = context.refreshDensityLayer(true);
  respond(pending[3], 1);
  await retry;
  assert.match(element("densityScaleLabels").innerHTML, /每格 1/);

  const inFlight = context.refreshDensityLayer(true);
  vm.runInContext('densityState.enabled = false; cancelDensityRequest(); updateDensityStyle();', context);
  respond(pending[4], 999);
  await inFlight;
  assert.equal(element("densityLegend").hidden, true);
  assert.equal(layers.get("ais-density-grid").layout.visibility, "none");
  context.clearDensityData();
  assert.equal(sources.get("ais-density-cells").data.features.length, 0);
  console.log("Density map: stale requests, layer toggles, retries, uniform counts and empty state passed");
})().catch((error) => { console.error(error); process.exitCode = 1; });
