const densityState = {
  enabled: true, showTracks: true, mode: "heatmap", opacity: 0.68, cellSize: 250,
  data: null, dataKey: "", requestKey: "", requestId: 0, controller: null, popup: null,
};
const DENSITY_COLORS = ["#2552a5", "#1f80bf", "#24b6bc", "#a3d67c", "#f5cd4c"];
const emptyDensity = () => ({ type: "FeatureCollection", features: [] });

window.addEventListener("DOMContentLoaded", () => {
  $("toggleDensityPanelBtn").addEventListener("click", () => toggleMapTool("density"));
  $("densityToggleBtn").addEventListener("click", () => {
    densityState.enabled = !densityState.enabled;
    if (!densityState.enabled) cancelDensityRequest();
    updateDensityStyle();
    refreshDensityLayer();
  });
  $("tracksToggleBtn").addEventListener("click", () => {
    densityState.showTracks = !densityState.showTracks;
    updateDensityStyle();
  });
  document.querySelectorAll("[data-density-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      densityState.mode = button.dataset.densityMode;
      removeDensityPopup();
      updateDensityStyle();
      renderDensityLegend();
    });
  });
  $("densityCellSizeSelect").addEventListener("change", () => {
    densityState.cellSize = Number($("densityCellSizeSelect").value);
    refreshDensityLayer();
  });
  $("densityOpacityInput").addEventListener("input", () => {
    densityState.opacity = Number($("densityOpacityInput").value) / 100;
    $("densityOpacityValue").textContent = `${Math.round(densityState.opacity * 100)}%`;
    updateDensityStyle();
  });
  $("retryDensityBtn").addEventListener("click", () => {
    densityState.enabled = true;
    updateDensityStyle();
    refreshDensityLayer(true);
  });
});

function initDensityLayers() {
  const map = state.map;
  map.addSource("ais-density-cells", { type: "geojson", data: emptyDensity() });
  map.addSource("ais-density-points", { type: "geojson", data: emptyDensity() });
  const before = "marker-region-fill";
  map.addLayer({
    id: "ais-density-heat", type: "heatmap", source: "ais-density-points",
    paint: {
      "heatmap-weight": ["get", "heat_weight"],
      "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 7, 12, 10, 18, 12, 25, 14, 42, 17, 70],
      "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 7, 1, 12, 1.3, 17, 1.8],
      "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"],
        0, "rgba(37,82,165,0)", 0.12, "rgba(37,82,165,0.55)",
        0.3, DENSITY_COLORS[1], 0.5, DENSITY_COLORS[2], 0.75, DENSITY_COLORS[3], 1, DENSITY_COLORS[4]],
      "heatmap-opacity": densityState.opacity,
    },
  }, before);
  map.addLayer({
    id: "ais-density-grid", type: "fill", source: "ais-density-cells",
    paint: {
      "fill-color": ["interpolate", ["linear"], ["get", "weight"],
        0, DENSITY_COLORS[0], 0.25, DENSITY_COLORS[1], 0.5, DENSITY_COLORS[2],
        0.75, DENSITY_COLORS[3], 1, DENSITY_COLORS[4]],
      "fill-opacity": densityState.opacity, "fill-antialias": false,
    },
  }, before);
  // Transparent hit targets allow heatmap users to inspect the underlying counts.
  map.addLayer({ id: "ais-density-hit", type: "fill", source: "ais-density-cells",
    paint: { "fill-color": "#ffffff", "fill-opacity": 0 } }, before);
  map.on("click", (event) => inspectDensityCell(event));
  // Enter inspects the center cell after keyboard map navigation.
  map.getCanvas().addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const center = map.getCenter();
    inspectDensityCell({ point: map.project(center), lngLat: center });
  });
  updateDensityStyle();
}

function inspectDensityCell(event) {
  if (!densityState.enabled || state.drawing || state.measuring || !densityState.data) return;
  const map = state.map;
  const markerLayers = ["marker-point", "marker-route-line", "marker-section-line", "marker-region-line"]
    .filter((id) => map.getLayer(id));
  if (markerLayers.length && map.queryRenderedFeatures(event.point, { layers: markerLayers }).length) return;
  const feature = map.queryRenderedFeatures(event.point, { layers: ["ais-density-hit"] })[0];
  if (!feature) return;
  removeDensityPopup();
  const properties = feature.properties;
  densityState.popup = new maplibregl.Popup({ offset: 10, maxWidth: "260px" })
    .setLngLat(event.lngLat).setHTML(`<div class="density-popup"><strong>网格内船舶活动</strong>
      <div><span>AIS 点数</span><b>${numberFormat.format(properties.point_count)}</b></div>
      <div><span>独立船舶</span><b>${numberFormat.format(properties.vessel_count)} 艘</b></div>
      <small>约 ${densitySizeLabel(densityState.data.metadata.cell_size_m)} 网格 · 当前筛选</small></div>`)
    .addTo(map);
}

function removeDensityPopup() {
  densityState.popup?.remove();
  densityState.popup = null;
}

function updateDensityStyle() {
  $("densityToggleBtn").setAttribute("aria-pressed", String(densityState.enabled));
  $("tracksToggleBtn").setAttribute("aria-pressed", String(densityState.showTracks));
  document.querySelectorAll("[data-density-mode]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.densityMode === densityState.mode));
  });
  const map = state.map;
  if (map && state.mapLoaded) {
    for (const [id, mode] of [["ais-density-heat", "heatmap"], ["ais-density-grid", "grid"], ["ais-density-hit", null]]) {
      if (!map.getLayer(id)) continue;
      map.setLayoutProperty(id, "visibility", densityState.enabled && (!mode || mode === densityState.mode) ? "visible" : "none");
    }
    if (map.getLayer("ais-density-heat")) map.setPaintProperty("ais-density-heat", "heatmap-opacity", densityState.opacity);
    if (map.getLayer("ais-density-grid")) map.setPaintProperty("ais-density-grid", "fill-opacity", densityState.opacity);
    if (map.getLayer("ais-tracks-raster")) {
      map.setLayoutProperty("ais-tracks-raster", "visibility", densityState.showTracks ? "visible" : "none");
      map.setPaintProperty("ais-tracks-raster", "raster-opacity", densityState.enabled ? 0.38 : 0.95);
    }
    if (map.getLayer("osm")) {
      map.setPaintProperty("osm", "raster-saturation", densityState.enabled ? -0.55 : 0);
      map.setPaintProperty("osm", "raster-contrast", densityState.enabled ? -0.12 : 0);
    }
  }
  $("densityLegend").hidden = !densityState.enabled || !state.manifest;
  if (!densityState.enabled) removeDensityPopup();
}

function cancelDensityRequest() {
  ++densityState.requestId;
  densityState.controller?.abort();
  densityState.controller = null;
  densityState.requestKey = "";
}

function clearDensityData() {
  cancelDensityRequest();
  densityState.data = null;
  densityState.dataKey = "";
  removeDensityPopup();
  state.map?.getSource("ais-density-cells")?.setData(emptyDensity());
  state.map?.getSource("ais-density-points")?.setData(emptyDensity());
  $("densityLegend").hidden = true;
  $("densityLegendBody").hidden = true;
  $("densityPanelStatus").textContent = "等待加载数据";
  $("densityPeakCount").textContent = $("densityActualSize").textContent = "—";
}

function densitySizeLabel(meters) {
  return meters >= 1000 ? `${formatNumber(meters / 1000, 1)} km` : `${numberFormat.format(meters)} m`;
}

function renderDensityLegend() {
  const meta = densityState.data?.metadata;
  const grid = densityState.mode === "grid";
  $("densityModeBadge").textContent = grid ? "网格" : "热力";
  const behaviorLabel = $("behaviorSelect").selectedOptions[0]?.text || "全部行为";
  const behavior = state.behaviorFilter ? behaviorLabel : "全部行为";
  const scope = state.spatialFilter?.mode === "section" ? "截面选中轨迹" : state.spatialFilter ? "所选区域" : "当前筛选";
  $("densityScope").textContent = `${behavior} · ${scope}`;
  if (!meta) return;
  $("densityLegendBody").hidden = !meta.cell_count;
  $("densityLegendStatus").hidden = Boolean(meta.cell_count);
  $("densityLegendStatus").textContent = "当前筛选范围没有可显示的 AIS 点。";
  document.querySelector(".density-colorbar").style.background = grid && meta.max_count === 1
    ? DENSITY_COLORS[4] : "";
  $("densityScaleLabels").innerHTML = grid
    ? (meta.max_count === 1 ? "<span>每格 1 个 AIS 点</span>"
      : `<span>1</span><span>点 / 网格</span><span>${numberFormat.format(meta.max_count)}</span>`)
    : "<span>低</span><span>相对热度</span><span>高</span>";
  $("densityPointCount").textContent = numberFormat.format(meta.point_count);
  $("densityCellCount").textContent = numberFormat.format(meta.cell_count);
  $("densityPeakCount").textContent = numberFormat.format(meta.max_count);
  $("densityActualSize").textContent = `约 ${densitySizeLabel(meta.cell_size_m)}`;
  $("densityLegendNote").textContent = grid
    ? `AIS 点 / 网格 · 对数色阶 · 约 ${densitySizeLabel(meta.cell_size_m)}`
    : `平滑热点 · 约 ${densitySizeLabel(meta.cell_size_m)} 聚合 · 点击查看计数`;
  const note = meta.coarsened ? "已自动合并网格，保留全部符合条件的点。" : "";
  $("densityPanelStatus").textContent = meta.cell_count ? `已统计 ${numberFormat.format(meta.point_count)} 个 AIS 点。${note}` : "当前筛选无数据，可调整筛选条件。";
}

async function refreshDensityLayer(force = false) {
  if (!state.mapLoaded || !state.manifest || !densityState.enabled) return;
  const url = `/api/density-grid${queryString({ cell_size_m: densityState.cellSize,
    behavior: state.behaviorFilter, data_version: state.activeKey })}`;
  if (!force && densityState.dataKey === url && densityState.data) {
    renderDensityLegend();
    return;
  }
  if (!force && densityState.requestKey === url) return;
  cancelDensityRequest();
  const requestId = densityState.requestId;
  const datasetKey = state.activeKey;
  const controller = new AbortController();
  densityState.controller = controller;
  densityState.requestKey = url;
  densityState.data = null;
  removeDensityPopup();
  state.map.getSource("ais-density-cells").setData(emptyDensity());
  state.map.getSource("ais-density-points").setData(emptyDensity());
  $("densityLegend").hidden = false;
  $("densityLegendBody").hidden = true;
  $("densityLegendStatus").hidden = false;
  $("densityLegendStatus").textContent = $("densityPanelStatus").textContent = "正在计算空间密度…";
  $("densityPeakCount").textContent = $("densityActualSize").textContent = "—";
  renderDensityLegend();
  try {
    const response = await (window.AIS ? window.AIS.request(url, { signal: controller.signal }) : fetch(url, { signal: controller.signal }));
    if (!response.ok) throw new Error(`密度请求失败 (${response.status})`);
    const data = await response.json();
    if (requestId !== densityState.requestId || datasetKey !== state.activeKey || !densityState.enabled) return;
    const maximum = Math.max(1, data.metadata.max_count);
    const cells = { type: "FeatureCollection", features: data.features.map((feature) => ({ ...feature,
      properties: { ...feature.properties, weight: maximum > 1 ? Math.log(feature.properties.point_count) / Math.log(maximum) : 1 },
    })) };
    const points = { type: "FeatureCollection", features: data.features.map((feature) => ({
      type: "Feature", properties: { heat_weight: Math.log1p(feature.properties.point_count) / Math.log1p(maximum) },
      geometry: { type: "Point", coordinates: [feature.properties.center_lon, feature.properties.center_lat] },
    })) };
    densityState.data = data;
    densityState.dataKey = url;
    state.map.getSource("ais-density-cells").setData(cells);
    state.map.getSource("ais-density-points").setData(points);
    updateDensityStyle();
    renderDensityLegend();
  } catch (error) {
    if (error.name === "AbortError" || requestId !== densityState.requestId) return;
    $("densityLegendStatus").textContent = $("densityPanelStatus").textContent = "密度加载失败，请点击“重新计算密度”重试。";
  } finally {
    if (requestId === densityState.requestId) {
      densityState.controller = null;
      densityState.requestKey = "";
    }
  }
}
