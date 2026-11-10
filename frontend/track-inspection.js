const trackInspector = {
  points: [], timer: null, viewportRequest: null, selectionRequest: null,
  frame: null, hoverKey: null, hoverPopup: null, marker: null, selectedPopup: null, selectionKey: null,
};
const TRACK_INSPECTION_ZOOM = 11;
const TRACK_MARKER_LAYERS = ["marker-point", "marker-route-line", "marker-section-line", "marker-region-line"];
const TRACK_INSPECTION_LAYERS = ["ais-inspect-dots", "ais-picked-outline", "ais-picked-line", "ais-picked-point"];
const TRACK_TIME_FORMAT = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
});

function trackFeatures(features = []) {
  return { type: "FeatureCollection", features };
}

function trackFeature(type, coordinates) {
  return { type: "Feature", properties: {}, geometry: { type, coordinates } };
}

function initTrackInspection() {
  const map = state.map;
  const before = "marker-region-fill";
  map.addSource("ais-inspect-points", { type: "geojson", data: trackFeatures() });
  map.addSource("ais-picked-track", { type: "geojson", data: trackFeatures() });
  map.addLayer({
    id: "ais-inspect-dots", type: "circle", source: "ais-inspect-points", minzoom: 13,
    paint: { "circle-radius": 2, "circle-color": "#d7f8fb", "circle-stroke-width": 0.7, "circle-stroke-color": "#075d77" },
  }, before);
  map.addLayer({
    id: "ais-picked-outline", type: "line", source: "ais-picked-track",
    filter: ["==", ["geometry-type"], "LineString"],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#173b51", "line-width": 8, "line-opacity": 0.88 },
  }, before);
  map.addLayer({
    id: "ais-picked-line", type: "line", source: "ais-picked-track",
    filter: ["==", ["geometry-type"], "LineString"],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#fbba39", "line-width": 4.5 },
  }, before);
  map.addLayer({
    id: "ais-picked-point", type: "circle", source: "ais-picked-track",
    filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-radius": 6, "circle-color": "#fbba39", "circle-stroke-color": "#173b51", "circle-stroke-width": 2 },
  }, before);
  map.on("movestart", clearTrackCandidates);
  map.on("moveend", scheduleTrackInspection);
  map.on("resize", scheduleTrackInspection);
  map.on("mousemove", (event) => {
    if (trackInspector.frame !== null) cancelAnimationFrame(trackInspector.frame);
    trackInspector.frame = requestAnimationFrame(() => {
      trackInspector.frame = null;
      const target = canInspectTracks() ? nearestTrackPoint(event.point) : null;
      showTrackPoint(target && !overTrackMarker(event.point) ? target : null);
    });
  });
  map.getCanvas().addEventListener("mouseleave", clearTrackHover);
  map.on("click", inspectTrackSelection);
}

function canInspectTracks() {
  return state.mapLoaded && densityState.showTracks && !state.drawing && !state.measuring
    && Boolean(state.manifest) && state.map.getZoom() >= TRACK_INSPECTION_ZOOM;
}

function setTrackInspectionHint(text) {
  const hint = $("trackInspectionHint");
  hint.hidden = !text;
  hint.textContent = text;
}

function clearTrackHover() {
  if (trackInspector.frame !== null) cancelAnimationFrame(trackInspector.frame);
  trackInspector.frame = null;
  trackInspector.hoverKey = null;
  trackInspector.hoverPopup?.remove();
  trackInspector.marker?.remove();
  trackInspector.hoverPopup = trackInspector.marker = null;
}

function clearTrackCandidates() {
  clearTimeout(trackInspector.timer);
  trackInspector.viewportRequest?.abort();
  trackInspector.viewportRequest = null;
  trackInspector.points = [];
  state.map?.getSource("ais-inspect-points")?.setData(trackFeatures());
  clearTrackHover();
}

function clearTrackSelection() {
  trackInspector.selectionRequest?.abort();
  trackInspector.selectionRequest = null;
  const popup = trackInspector.selectedPopup;
  trackInspector.selectedPopup = null;
  trackInspector.selectionKey = null;
  popup?.remove();
  state.map?.getSource("ais-picked-track")?.setData(trackFeatures());
}

function resetTrackInspection() {
  clearTrackCandidates();
  clearTrackSelection();
  setTrackInspectionHint("");
}

function scheduleTrackInspection() {
  clearTrackCandidates();
  if (!state.manifest || !densityState.showTracks) {
    setTrackInspectionHint("");
    return;
  }
  if (state.map.getZoom() < TRACK_INSPECTION_ZOOM) {
    setTrackInspectionHint("放大地图后可查看轨迹点并选中轨迹段。");
    return;
  }
  setTrackInspectionHint("正在加载可点选的轨迹点…");
  trackInspector.timer = setTimeout(loadTrackCandidates, 180);
}

function trackViewportWindows(bounds) {
  const span = bounds.getEast() - bounds.getWest();
  const west = ((bounds.getWest() + 180) % 360 + 360) % 360 - 180;
  const south = Math.max(-90, bounds.getSouth());
  const north = Math.min(90, bounds.getNorth());
  const east = west + span;
  if (span >= 360) return [{ west: -180, south, east: 180, north }];
  if (east <= 180) return [{ west, south, east, north }];
  return [{ west, south, east: 180, north }, { west: -180, south, east: east - 360, north }];
}

async function trackInspectionJson(path, params, signal) {
  const response = await authenticatedFetch(path + params, { signal });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === "string" ? error.detail : "轨迹查询失败，请重试。");
  }
  return response.json();
}

async function loadTrackCandidates() {
  const map = state.map;
  const controller = new AbortController();
  trackInspector.viewportRequest = controller;
  try {
    const windows = trackViewportWindows(map.getBounds());
    const results = await Promise.all(windows.map((bounds) => trackInspectionJson(
      "/api/track-inspection/viewport",
      queryString({ ...bounds, behavior: state.behaviorFilter, data_version: state.activeKey }),
      controller.signal,
    )));
    if (trackInspector.viewportRequest !== controller || controller.signal.aborted) return;
    const center = map.getCenter().lng;
    const visibleCoordinate = (lon, lat) => [lon + 360 * Math.round((center - lon) / 360), lat];
    trackInspector.points = results.flatMap((result) => result.points).map((point) => ({
      ...point,
      coordinate: visibleCoordinate(point.lon, point.lat),
      pixel: map.project(visibleCoordinate(point.lon, point.lat)),
      previousPixel: point.previous ? map.project(visibleCoordinate(...point.previous)) : null,
    }));
    map.getSource("ais-inspect-points").setData(trackFeatures(
      trackInspector.points.map((point) => trackFeature("Point", point.coordinate)),
    ));
    setTrackInspectionHint(results.some((result) => result.limited)
      ? "当前视窗轨迹较密，放大地图可查看更多点位。"
      : "悬停查看轨迹点 · 点击点或线选中轨迹段");
  } catch (error) {
    if (error.name !== "AbortError" && trackInspector.viewportRequest === controller) {
      setTrackInspectionHint(error.message);
    }
  } finally {
    if (trackInspector.viewportRequest === controller) trackInspector.viewportRequest = null;
  }
}

function overTrackMarker(pixel) {
  const layers = TRACK_MARKER_LAYERS.filter((layer) => state.map.getLayer(layer));
  return layers.length > 0 && state.map.queryRenderedFeatures(pixel, { layers }).length > 0;
}

function nearestTrackPoint(pixel) {
  let result = null;
  let distance = 8 ** 2;
  for (const point of trackInspector.points) {
    const candidate = (pixel.x - point.pixel.x) ** 2 + (pixel.y - point.pixel.y) ** 2;
    if (candidate >= distance) continue;
    result = point;
    distance = candidate;
  }
  return result;
}

function nearestTrackLine(pixel) {
  let result = null;
  let distance = 7 ** 2;
  for (const point of trackInspector.points) {
    if (!point.previousPixel) continue;
    const from = point.previousPixel;
    const dx = point.pixel.x - from.x;
    const dy = point.pixel.y - from.y;
    const lengthSquared = dx * dx + dy * dy;
    if (!lengthSquared) continue;
    const t = Math.max(0, Math.min(1, ((pixel.x - from.x) * dx + (pixel.y - from.y) * dy) / lengthSquared));
    const candidate = (pixel.x - from.x - t * dx) ** 2 + (pixel.y - from.y - t * dy) ** 2;
    if (candidate >= distance) continue;
    result = point;
    distance = candidate;
  }
  return result;
}

function trackNumber(value, unit = "", digits = 2) {
  return Number.isFinite(value) ? `${formatNumber(value, digits)}${unit}` : "-";
}

function trackCoordinate(value) {
  return Number.isFinite(value) ? value.toFixed(6) + "°" : "-";
}

function trackCard(title, mmsi, rows) {
  const card = document.createElement("article");
  card.className = "track-inspection-card";
  const heading = document.createElement("strong");
  heading.className = "track-inspection-title";
  heading.textContent = title;
  const identity = document.createElement("div");
  identity.className = "track-inspection-mmsi";
  identity.textContent = `MMSI ${mmsi ?? "-"}`;
  const grid = document.createElement("dl");
  grid.className = "track-inspection-grid";
  for (const [label, value] of rows) {
    const cell = document.createElement("div");
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = value;
    cell.append(term, description);
    grid.append(cell);
  }
  card.append(heading, identity, grid);
  return card;
}

function trackPointKey(point) {
  return `${point.mmsi}:${point.track_id}:${point.timestamp}:${point.lon}:${point.lat}`;
}

function showTrackPoint(point) {
  const key = point ? trackPointKey(point) : null;
  if (key === trackInspector.selectionKey) point = null;
  if (point && key === trackInspector.hoverKey) return;
  clearTrackHover();
  if (!point) return;
  trackInspector.hoverKey = key;
  const marker = document.createElement("div");
  marker.className = "track-hover-marker";
  const dot = document.createElement("span");
  marker.append(dot);
  trackInspector.marker = new maplibregl.Marker({ element: marker }).setLngLat(point.coordinate).addTo(state.map);
  const time = new Date(point.timestamp * 1000);
  const card = trackCard("AIS 轨迹点", point.mmsi, [
    ["时间 · 北京", point.timestamp > 0 && Number.isFinite(time.getTime()) ? TRACK_TIME_FORMAT.format(time) : "-"],
    ["船长 / 船宽", `${trackNumber(point.length, " m")} / ${trackNumber(point.width, " m")}`],
    ["经度", trackCoordinate(point.lon)],
    ["纬度", trackCoordinate(point.lat)],
    ["航速 · SOG", trackNumber(point.speed, " kn")],
    ["对地航向 · COG", trackNumber(point.course, "°")],
    ["船首向 · Heading", trackNumber(point.heading, "°")],
  ]);
  trackInspector.hoverPopup = new maplibregl.Popup({
    className: "track-inspection-popup track-hover-popup",
    closeButton: false, closeOnClick: false, focusAfterOpen: false, maxWidth: "300px", offset: 12,
  }).setLngLat(point.coordinate).setDOMContent(card).addTo(state.map);
}

async function inspectTrackSelection(event) {
  if (!canInspectTracks() || overTrackMarker(event.point)) return;
  const hitPoint = nearestTrackPoint(event.point);
  const point = hitPoint || nearestTrackLine(event.point);
  clearTrackSelection();
  if (!point) return;
  clearTrackHover();
  trackInspector.selectionKey = trackPointKey(point);
  removeDensityPopup();
  const controller = new AbortController();
  trackInspector.selectionRequest = controller;
  const params = queryString({
    behavior: state.behaviorFilter, data_version: state.activeKey,
    vessel_id: point.mmsi, track_id: point.track_id,
    anchor_time: point.timestamp, anchor_lon: point.lon, anchor_lat: point.lat,
  });
  try {
    const detail = await trackInspectionJson("/api/track-inspection/segment", params, controller.signal);
    if (trackInspector.selectionRequest !== controller || controller.signal.aborted) return;
    const map = state.map;
    const distinct = detail.path.some(([lon, lat]) => lon !== detail.path[0][0] || lat !== detail.path[0][1]);
    const feature = distinct ? trackFeature("LineString", detail.path) : trackFeature("Point", detail.path[0]);
    map.getSource("ais-picked-track").setData(trackFeatures([feature]));
    const card = trackCard("已选轨迹段", detail.mmsi, [
      ["船长", trackNumber(detail.length, " m")],
      ["船宽", trackNumber(detail.width, " m")],
      ["轨迹点数", trackNumber(detail.point_count, " 个", 0)],
      ["平均航速", trackNumber(detail.speed_avg, " kn")],
    ]);
    const download = document.createElement("a");
    download.className = "btn dark full track-export-link";
    download.textContent = "导出该轨迹数据";
    download.href = "/api/export/selected-track" + params;
    card.append(download);
    const popup = new maplibregl.Popup({
      className: "track-inspection-popup track-selection-popup",
      closeOnClick: false, focusAfterOpen: false, maxWidth: "300px", offset: 14,
    }).setLngLat(hitPoint ? point.coordinate : event.lngLat).setDOMContent(card).addTo(map);
    trackInspector.selectedPopup = popup;
    popup.on("close", () => {
      if (trackInspector.selectedPopup === popup) clearTrackSelection();
    });
  } catch (error) {
    if (error.name !== "AbortError" && trackInspector.selectionRequest === controller) {
      trackInspector.selectionKey = null;
      setTrackInspectionHint(error.message);
    }
  } finally {
    if (trackInspector.selectionRequest === controller) trackInspector.selectionRequest = null;
  }
}
