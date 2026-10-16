const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const elements = new Map();
const element = (id) => {
  if (!elements.has(id)) elements.set(id, { innerHTML: "", textContent: "", value: "", disabled: false });
  return elements.get(id);
};
const stored = new Map();
const markerSource = { data: null, setData(data) { this.data = data; } };
const counters = ["point", "section", "route", "region"].map((type) => ({
  dataset: { markerCount: type }, textContent: "",
}));
const context = vm.createContext({
  window: { addEventListener() {} },
  document: {
    getElementById: element,
    querySelectorAll(selector) { return selector === "[data-marker-count]" ? counters : []; },
  },
  localStorage: {
    getItem(key) { return stored.get(key) || null; },
    setItem(key, value) { stored.set(key, value); },
  },
  AIS: { user: { id: 7 } },
  markerSource, Intl, Math, console,
});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/app.js"), "utf8"), context);
vm.runInContext(`
  state.map = { getSource(id) { return id === "saved-markers" ? markerSource : null; } };
  state.markers = [
    { id: 1, type: "section", name: "截面一", category: "未分类", color: "#7c3aed", visible: true, points: [[110,20],[111,21]] },
    { id: 2, type: "region", areaShape: "polygon", name: "区域一", category: "未分类", color: "#f43f5e", visible: true, points: [[110,20],[111,20],[111,21]] },
    { id: 3, type: "point", name: "点一", category: "未分类", color: "#2563eb", visible: true, points: [[110,20]] },
  ];
  renderMarkers();
`, context);
assert.equal(markerSource.data.features.length, 3);
assert.match(element("markerList").innerHTML, /#i-eye/);
assert.doesNotMatch(element("markerList").innerHTML, /点一[\s\S]*toggleMarkerVisibility\(3\)/);

vm.runInContext("toggleMarkerVisibility(1)", context);
assert.equal(markerSource.data.features.length, 2);
assert.equal(markerSource.data.features.some((feature) => feature.properties.markerId === "1"), false);
assert.match(element("markerList").innerHTML, /#i-eye-off/);
assert.equal(JSON.parse(stored.get("ais-platform-markers:user_7"))[0].visible, false);

vm.runInContext("toggleMarkerVisibility(1)", context);
assert.equal(markerSource.data.features.length, 3);
assert.equal(JSON.parse(stored.get("ais-platform-markers:user_7"))[0].visible, true);

stored.set("ais-platform-markers:user_7", JSON.stringify([
  { id: 4, type: "region", areaShape: "polygon", name: "旧区域", category: "未分类", color: "#f43f5e", points: [[110,20],[111,20],[111,21]] },
]));
vm.runInContext("loadMarkers(); renderMarkers();", context);
assert.equal(markerSource.data.features.length, 1, "Old markers without visibility state stay visible");
assert.equal(vm.runInContext("state.markers[0].visible", context), true);

console.log("Marker visibility: default, hide/show, persistence and legacy migration passed");
