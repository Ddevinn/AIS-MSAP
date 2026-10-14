const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function fixture() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      textContent: "", classes: new Set(), listeners: new Map(),
      classList: { toggle(name, on) { on ? element(id).classes.add(name) : element(id).classes.delete(name); }, remove(name) { element(id).classes.delete(name); } },
      addEventListener(name, callback) { this.listeners.set(name, callback); },
      removeEventListener(name) { this.listeners.delete(name); },
    });
    return elements.get(id);
  };
  let loaded = false, zoom = 10;
  const listeners = new Map(), timers = new Map(), reloads = [];
  const source = { setTiles(tiles) { loaded = false; reloads.push(tiles); } };
  const map = {
    getSource: () => source, isSourceLoaded: () => loaded, getZoom: () => zoom,
    getBounds: () => ({ getWest: () => 109, getEast: () => 112, getSouth: () => 19, getNorth: () => 21 }),
    on(name, callback) { listeners.set(name, callback); }, off(name) { listeners.delete(name); },
  };
  const context = vm.createContext({ window: {}, document: { getElementById: element }, console: { warn() {} },
    setTimeout(callback, delay) { const id = Symbol(); timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/basemap.js"), "utf8"), context);
  const controller = context.window.AISBasemap.attach(map);
  const event = (status = 0, id = "tile-1", sourceId = "osm", canonical = { z: 11, x: 1651, y: 906 }) => ({
    sourceId, error: { status, url: sourceId === "osm" ? `https://tile.openstreetmap.org/11/1651/906.png` : "/api/tiles/tracks/11/1651/906.png" },
    tile: { tileID: { key: id, canonical, wrap: 0 }, state: "errored" },
  });
  const runTimer = () => { const [id, timer] = timers.entries().next().value; timers.delete(id); timer.callback(); };
  return { elements, element, listeners, timers, reloads, controller, event, runTimer,
    load() { loaded = true; }, setZoom(value) { zoom = value; } };
}

// A completed source with an errored tile is not a healthy basemap; clearing
// AIS results/status polling must not be able to erase the independent warning.
{
  const f = fixture();
  f.load();
  assert.equal(f.controller.handleError(f.event(403)), true);
  f.listeners.get("idle")();
  assert.match(f.element("basemapStatusText").textContent, /未加载/);
  assert(f.element("basemapStatus").classes.has("has-error"));
  assert.equal(f.timers.size, 0, "Do not automatically retry policy rejection");
  f.element("mapStatus").textContent = "等待加载数据";
  assert.match(f.element("basemapStatusText").textContent, /未加载/);
  assert.equal(f.controller.handleError(f.event(500, "tile-2")), true);
  assert.equal(f.timers.size, 0, "A later transient error must not bypass a rate/policy rejection");
  f.element("retryBasemapBtn").listeners.get("click")();
  assert.equal(f.reloads.length, 1);
  assert.equal(f.reloads[0][0], "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "Retry keeps cacheable URLs");
  assert.match(f.element("basemapStatusText").textContent, /重新加载/);
  assert.equal(f.element("basemapStatus").classes.has("has-error"), false);
}

// Coalesce simultaneous failures, retry no more than twice, and leave a useful
// manual recovery action when the external provider remains unavailable.
{
  const f = fixture();
  f.controller.handleError(f.event(0));
  f.controller.handleError(f.event(503, "tile-2"));
  assert.equal(f.timers.size, 1);
  f.runTimer();
  assert.equal(f.reloads.length, 1);
  f.controller.handleError(f.event(0));
  f.runTimer();
  f.controller.handleError(f.event(503));
  assert.equal(f.reloads.length, 2);
  assert.equal(f.timers.size, 0);
  assert.match(f.element("basemapStatusText").textContent, /检查网络/);
  f.controller.handleError(f.event(503, "outside", "osm", { z: 11, x: 100, y: 100 }));
  f.setZoom(17);
  f.listeners.get("moveend")();
  assert.equal(f.element("basemapStatus").classes.has("has-error"), false, "Failures at other zooms do not describe the new viewport");
}

// Successful retry clears the matching error and cancels pending retries;
// source isolation keeps AIS failures from refreshing the external basemap.
{
  const f = fixture();
  assert.equal(f.controller.handleError(f.event(500, "track", "ais-tracks")), false);
  assert.equal(f.timers.size, 0);
  f.controller.handleError(f.event(0));
  f.load();
  const recovered = f.event(); recovered.tile.state = "loaded";
  f.listeners.get("sourcedata")(recovered);
  assert.equal(f.element("basemapStatusText").textContent, "底图已加载");
  assert.equal(f.timers.size, 0);
  f.controller.handleError(f.event(0));
  f.controller.dispose();
  assert.equal(f.timers.size, 0);
  assert.equal(f.listeners.size, 0);
}
console.log("Basemap: source isolation, persistent failure status, bounded retries and recovery passed");
