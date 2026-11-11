const vesselHistory = {
  profile: null,
  scope: null,
  identityTimer: null,
  result: null,
  pending: null,
  revision: 0,
  chartWindow: null,
  exporting: false,
};

const vesselHistoryLayers = ["vessel-history-outline", "vessel-history-line", "vessel-history-point", "vessel-history-vertices"];
const vesselHistoryFeatures = () => ({ type: "FeatureCollection", features: [] });
const vesselChartFields = {
  speed: { title: "航速分析", name: "航速", unit: " kn", color: "#2563eb" },
  course: { title: "对地航向分析", name: "对地航向", unit: "°", color: "#087e94" },
  heading: { title: "船首向分析", name: "船首向", unit: "°", color: "#6651ba" },
};
const vesselDateTime = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Shanghai", hourCycle: "h23", year: "numeric", month: "2-digit",
  day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
});

function vesselDate(timestamp) {
  return vesselDateTime.format(timestamp * 1000);
}

function initVesselHistory() {
  $("vesselSearch").addEventListener("submit", searchVessel);
  $("vesselMmsi").addEventListener("input", () => showVesselSearchStatus(""));
  $("closeVesselCard").addEventListener("click", () => {
    $("vesselCard").hidden = true;
    $("vesselMmsi").focus();
  });
  $("vesselPeriod").addEventListener("submit", (event) => {
    event.preventDefault();
    requestVesselHistory(true);
  });
  $("vesselPeriod").addEventListener("input", () => {
    clearVesselPeriod();
    $("vesselQueryStatus").textContent = "";
  });
  $("exportVesselTrack").addEventListener("click", exportVesselHistory);
  document.querySelectorAll("[data-vessel-chart]").forEach((button) => {
    button.addEventListener("click", () => openVesselChart(button.dataset.vesselChart));
  });
  enableVesselDrag($("vesselCard"));
  const observer = new ResizeObserver(() => {
    if (!$("vesselCard").hidden) placeVesselCard();
    const panel = vesselHistory.chartWindow?.panel;
    if (!panel) return;
    if (panel.classList.contains("is-dragged")) placeVesselWindow(panel, panel.offsetLeft, panel.offsetTop);
    else placeVesselChart(panel);
  });
  observer.observe($("vesselOverlays"));
}

function showVesselSearchStatus(message) {
  const status = $("vesselSearchStatus");
  status.textContent = message;
  status.hidden = !message;
}

function resetVesselHistory() {
  $("vesselMmsi").value = "";
  showVesselSearchStatus("");
  clearVesselSearch();
}

function clearVesselSearch() {
  clearTimeout(vesselHistory.identityTimer);
  vesselHistory.identityTimer = null;
  vesselHistory.scope = vesselHistory.profile = null;
  $("vesselCard").hidden = true;
  $("vesselCard").classList.remove("is-dragged");
  clearVesselPeriod();
}

function clearVesselPeriod() {
  vesselHistory.revision += 1;
  vesselHistory.result = vesselHistory.pending = null;
  closeVesselChart();
  state.map?.getSource("vessel-history")?.setData(vesselHistoryFeatures());
  state.map?.getSource("vessel-history-location")?.setData(vesselHistoryFeatures());
  updateVesselActions();
}

function vesselScope() {
  const { mmsi, start_ts, end_ts } = state.filters;
  const params = new URLSearchParams(queryString({
    data_version: state.activeKey, behavior: state.behaviorFilter,
    mmsi: null, start_ts: null, end_ts: null,
    filter_mmsi: mmsi, filter_start_ts: start_ts, filter_end_ts: end_ts,
  }));
  params.sort();
  return params.toString();
}

function syncVesselScope() {
  if (vesselHistory.scope !== null && vesselHistory.scope !== vesselScope()) {
    clearVesselSearch();
    showVesselSearchStatus("");
  }
}

function vesselParams(mmsi, period = {}) {
  const params = new URLSearchParams(vesselHistory.scope);
  params.set("mmsi", mmsi);
  for (const [key, value] of Object.entries(period)) params.set(key, value);
  return params;
}

async function searchVessel(event) {
  event.preventDefault();
  const mmsi = $("vesselMmsi").value.trim();
  if (!state.activeKey) return;
  if (!/^[1-9]\d{8}$/.test(mmsi)) {
    showVesselSearchStatus("请输入 9 位 MMSI。");
    return;
  }
  syncVesselScope();
  if (String(vesselHistory.profile?.mmsi) === mmsi) {
    $("vesselCard").hidden = false;
    placeVesselCard();
    bringVesselWindowForward($("vesselCard"));
    showVesselSearchStatus("");
    return;
  }
  clearVesselSearch();
  vesselHistory.scope = vesselScope();
  const revision = vesselHistory.revision;
  showVesselSearchStatus("正在搜索船舶…");
  try {
    const url = "/api/vessels/profile?" + vesselParams(mmsi);
    const { vessel } = await api(url);
    syncVesselScope();
    if (revision !== vesselHistory.revision || $("vesselMmsi").value.trim() !== mmsi) return;
    const filtered = Object.keys(state.filters).length > 0 || state.behaviorFilter;
    showVesselSearchStatus(vessel ? "" : filtered ? "当前筛选范围内未搜索到船舶" : "未搜索到船舶");
    if (vessel) {
      renderVesselCard(vessel);
      scheduleVesselIdentity(vessel, url);
    }
  } catch (error) {
    if (revision === vesselHistory.revision && $("vesselMmsi").value.trim() === mmsi) {
      showVesselSearchStatus(error.message || "船舶搜索失败。");
    }
  }
}

function scheduleVesselIdentity(profile, url) {
  if (!profile.identity_pending) return;
  vesselHistory.identityTimer = setTimeout(async () => {
    vesselHistory.identityTimer = null;
    try {
      const { vessel } = await api(url);
      syncVesselScope();
      if (vesselHistory.profile !== profile) return;
      if (vessel?.identity_pending) {
        scheduleVesselIdentity(profile, url);
        return;
      }
      Object.assign(profile, {
        ship_name: vessel?.ship_name, imo: vessel?.imo, callsign: vessel?.callsign, identity_pending: false,
      });
    } catch (error) {
      if (vesselHistory.profile !== profile) return;
      profile.identity_pending = false;
      console.error("Vessel identity lookup", error);
    }
    renderVesselDetails(profile);
  }, 4000);
}

function vesselMeasure(value, digits, unit) {
  return value == null ? "-" : formatNumber(value, digits) + unit;
}

function renderVesselDetails(vessel) {
  const missingIdentity = vessel.identity_pending ? "正在补查…" : "-";
  $("vesselCardTitle").textContent = vessel.ship_name || "MMSI " + vessel.mmsi;
  const fields = [
    ["船名", vessel.ship_name || missingIdentity, "wide"],
    ["MMSI", vessel.mmsi],
    ["IMO", vessel.imo || missingIdentity],
    ["呼号", vessel.callsign || missingIdentity],
    ["船舶尺寸（长 × 宽）", vesselMeasure(vessel.length, 1, " m") + " × " + vesselMeasure(vessel.width, 1, " m")],
    ["吃水", vesselMeasure(vessel.draught, 2, " m")],
    ["船首向", vesselMeasure(vessel.heading, 1, "°")],
    ["对地航向", vesselMeasure(vessel.course, 1, "°")],
    ["航速", vesselMeasure(vessel.speed, 2, " kn")],
    ["经纬度", vessel.lon.toFixed(6) + "°, " + vessel.lat.toFixed(6) + "°", "wide"],
    [Object.keys(state.filters).length || state.behaviorFilter ? "筛选范围内最近更新时间" : "历史最近更新时间",
      vesselDate(vessel.end_ts), "wide"],
  ];
  $("vesselDetails").innerHTML = fields.map(([name, value, width]) =>
    '<div class="' + (width || "") + '"><dt>' + name + '</dt><dd>' + escapeHtml(value) + '</dd></div>'
  ).join("");
}

function renderVesselCard(vessel) {
  vesselHistory.profile = vessel;
  renderVesselDetails(vessel);
  const start = vesselDate(vessel.start_ts).replace(" ", "T");
  const end = vesselDate(vessel.end_ts).replace(" ", "T");
  for (const input of [$("vesselStart"), $("vesselEnd")]) {
    input.min = start;
    input.max = end;
    input.value = input === $("vesselStart") ? start : end;
  }
  $("vesselQueryStatus").textContent = "";
  const card = $("vesselCard");
  card.hidden = false;
  placeVesselCard();
  bringVesselWindowForward(card);
  updateVesselActions();
}

function updateVesselActions() {
  const waiting = Boolean(vesselHistory.pending);
  $("queryVesselTrack").disabled = waiting || !vesselHistory.profile;
  $("exportVesselTrack").disabled = vesselHistory.exporting || !vesselHistory.result?.point_count;
  for (const button of document.querySelectorAll("[data-vessel-chart]")) {
    button.disabled = waiting || !vesselHistory.profile;
  }
}

async function requestVesselHistory(fit = false) {
  syncVesselScope();
  const profile = vesselHistory.profile;
  if (!profile || !$("vesselPeriod").reportValidity()) return null;
  const start = Date.parse($("vesselStart").value + "+08:00") / 1000;
  const end = Date.parse($("vesselEnd").value + "+08:00") / 1000;
  const status = $("vesselQueryStatus");
  if (start > end) {
    status.textContent = "开始时间不能晚于结束时间。";
    return null;
  }
  if (vesselHistory.result) {
    if (fit && vesselHistory.result.point_count) showVesselHistoryOnMap(vesselHistory.result, true);
    return vesselHistory.result;
  }
  if (vesselHistory.pending) return vesselHistory.pending;
  const revision = vesselHistory.revision;
  const params = vesselParams(profile.mmsi, { start_ts: start, end_ts: end });
  status.textContent = "正在查询历史轨迹…";
  vesselHistory.pending = (async () => {
    try {
      const result = await api("/api/vessels/history?" + params);
      syncVesselScope();
      if (revision !== vesselHistory.revision) return null;
      result.params = params;
      vesselHistory.result = result;
      showVesselHistoryOnMap(result, fit || result.point_count > 0);
      status.textContent = result.point_count
        ? "已高亮 " + numberFormat.format(result.point_count) + " 个 AIS 点"
        : "该时段未查询到轨迹点。";
      return result;
    } catch (error) {
      if (revision === vesselHistory.revision) status.textContent = error.message || "轨迹查询失败。";
      return null;
    } finally {
      if (revision === vesselHistory.revision) {
        vesselHistory.pending = null;
        updateVesselActions();
      }
    }
  })();
  updateVesselActions();
  return vesselHistory.pending;
}

async function exportVesselHistory() {
  syncVesselScope();
  const result = vesselHistory.result;
  if (!result?.point_count || vesselHistory.exporting) return;
  vesselHistory.exporting = true;
  updateVesselActions();
  try {
    const response = await authenticatedFetch("/api/export/vessel-history?" + result.params);
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || "轨迹导出失败。");
    }
    const blob = await response.blob();
    syncVesselScope();
    if (vesselHistory.result !== result) return;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "AIS_" + result.params.get("mmsi") + "_历史轨迹.xlsx";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (error) {
    if (vesselHistory.result === result) $("vesselQueryStatus").textContent = error.message;
  } finally {
    vesselHistory.exporting = false;
    updateVesselActions();
  }
}

function initVesselHistoryLayers() {
  const map = state.map;
  map.addSource("vessel-history", { type: "geojson", data: vesselHistoryFeatures() });
  map.addSource("vessel-history-location", { type: "geojson", data: vesselHistoryFeatures() });
  const line = {
    type: "line", source: "vessel-history",
    filter: ["==", ["geometry-type"], "LineString"],
    layout: { "line-cap": "round", "line-join": "round" },
  };
  map.addLayer({ ...line, id: vesselHistoryLayers[0], paint: { "line-color": "#173854", "line-width": 7 } });
  map.addLayer({ ...line, id: vesselHistoryLayers[1], paint: { "line-color": "#47e3e1", "line-width": 4 } });
  map.addLayer({
    id: vesselHistoryLayers[2], type: "circle", source: "vessel-history",
    filter: ["==", ["get", "role"], "isolated"],
    paint: { "circle-color": "#47e3e1", "circle-radius": 5, "circle-stroke-color": "#173854", "circle-stroke-width": 2 },
  });
  map.addLayer({
    id: vesselHistoryLayers[3], type: "circle", source: "vessel-history", minzoom: 13,
    filter: ["==", ["get", "role"], "vertex"],
    paint: { "circle-color": "#0d657b", "circle-radius": 3.4, "circle-stroke-color": "#fff", "circle-stroke-width": 1.3 },
  });
  map.addLayer({
    id: "vessel-history-focus", type: "circle", source: "vessel-history-location",
    paint: { "circle-color": "#146bc3", "circle-radius": 9, "circle-stroke-color": "#fff", "circle-stroke-width": 3 },
  });
  raiseVesselHistoryLayers();
  if (vesselHistory.result) showVesselHistoryOnMap(vesselHistory.result, true);
}

function raiseVesselHistoryLayers() {
  const map = state.map;
  for (const id of vesselHistoryLayers) {
    if (map.getLayer(id)) map.moveLayer(id, "ais-inspect-dots");
  }
  if (map.getLayer("vessel-history-focus")) map.moveLayer("vessel-history-focus");
}

function showVesselHistoryOnMap(result, fit) {
  const map = state.map;
  const source = map?.getSource("vessel-history");
  if (!source) return;
  const data = vesselHistoryFeatures();
  const bounds = new maplibregl.LngLatBounds();
  for (const coordinates of result.segments) {
    const geometry = coordinates.length === 1
      ? { type: "Point", coordinates: coordinates[0] }
      : { type: "LineString", coordinates };
    data.features.push({ type: "Feature", geometry, properties: { role: coordinates.length === 1 ? "isolated" : "line" } });
    if (coordinates.length > 1) {
      data.features.push({ type: "Feature", geometry: { type: "MultiPoint", coordinates }, properties: { role: "vertex" } });
    }
    if (fit) for (const coordinate of coordinates) bounds.extend(coordinate);
  }
  source.setData(data);
  if (!bounds.isEmpty()) {
    const { x, y } = vesselMapViewPoint();
    const canvas = map.getCanvas();
    const padding = { left: 32, right: 32, top: 38, bottom: 38 };
    if (x < canvas.clientWidth * 0.4) padding.right += Math.round(canvas.clientWidth - 2 * x);
    if (x > canvas.clientWidth * 0.6) padding.left += Math.round(2 * x - canvas.clientWidth);
    if (y > canvas.clientHeight * 0.6) padding.top += Math.round(2 * y - canvas.clientHeight);
    map.fitBounds(bounds, { padding, maxZoom: 15, duration: 650 });
  }
}

function vesselMapViewPoint() {
  const overlay = $("vesselOverlays");
  const width = overlay.clientWidth;
  const height = $("map").clientHeight;
  const visible = [$("vesselCard"), vesselHistory.chartWindow?.panel].filter((panel) => panel && !panel.hidden);
  const candidates = [[0.5, 0.5], [0.25, 0.55], [0.75, 0.55], [0.5, 0.8], [0.25, 0.8], [0.75, 0.8]];
  for (const [column, row] of candidates) {
    const x = width * column;
    const y = height * row;
    if (visible.every((panel) => x < panel.offsetLeft - 16 || x > panel.offsetLeft + panel.offsetWidth + 16
        || y < panel.offsetTop - 16 || y > panel.offsetTop + panel.offsetHeight + 16)) return { x, y };
  }
  return { x: width / 2, y: height / 2 };
}

function locateVesselPoint(point) {
  const map = state.map;
  const source = map?.getSource("vessel-history-location");
  if (!source) return;
  const coordinates = [point.lon, point.lat];
  const data = vesselHistoryFeatures();
  data.features.push({ type: "Feature", geometry: { type: "Point", coordinates }, properties: {} });
  source.setData(data);
  const target = vesselMapViewPoint();
  const canvas = map.getCanvas();
  map.easeTo({
    center: coordinates,
    offset: [target.x - canvas.clientWidth / 2, target.y - canvas.clientHeight / 2],
    zoom: Math.max(map.getZoom(), 13),
    duration: 550,
  });
}

function placeVesselCard() {
  const card = $("vesselCard");
  const overlay = $("vesselOverlays").getBoundingClientRect();
  const search = $("vesselSearch").getBoundingClientRect();
  placeVesselWindow(card, card.classList.contains("is-dragged") ? card.offsetLeft : search.left - overlay.left,
    card.classList.contains("is-dragged") ? card.offsetTop : 12);
}

function placeVesselWindow(panel, x, y) {
  const margin = 8;
  const overlay = $("vesselOverlays");
  panel.style.left = Math.max(margin, Math.min(x, overlay.clientWidth - panel.offsetWidth - margin)) + "px";
  panel.style.top = Math.max(margin, Math.min(y, overlay.clientHeight - panel.offsetHeight - margin)) + "px";
}

function bringVesselWindowForward(panel) {
  $("vesselOverlays").querySelectorAll(".vessel-window").forEach((window) => window.classList.toggle("is-front", window === panel));
}

function enableVesselDrag(panel) {
  const handle = panel.querySelector(".vessel-window-head");
  let drag = null;
  panel.addEventListener("pointerdown", () => bringVesselWindowForward(panel));
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("button")) return;
    drag = { pointer: event.pointerId, x: event.clientX - panel.offsetLeft, y: event.clientY - panel.offsetTop };
    handle.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  handle.addEventListener("pointermove", (event) => {
    if (drag?.pointer !== event.pointerId) return;
    panel.classList.add("is-dragged");
    placeVesselWindow(panel, event.clientX - drag.x, event.clientY - drag.y);
  });
  handle.addEventListener("lostpointercapture", () => { drag = null; });
}

function closeVesselChart() {
  const item = vesselHistory.chartWindow;
  if (!item) return;
  item.chart?.destroy();
  item.panel.remove();
  vesselHistory.chartWindow = null;
}

async function openVesselChart(field) {
  const result = await requestVesselHistory();
  if (!result?.point_count || vesselHistory.result !== result) return;
  const existing = vesselHistory.chartWindow;
  if (existing?.field === field) {
    bringVesselWindowForward(existing.panel);
    return;
  }
  closeVesselChart();
  const config = vesselChartFields[field];
  const panel = document.createElement("section");
  panel.className = "vessel-window vessel-chart";
  panel.setAttribute("aria-label", config.title);
  panel.innerHTML = '<header class="vessel-window-head"><div><span class="eyebrow">MMSI '
    + vesselHistory.profile.mmsi + '</span><h3>' + config.title + '</h3></div>'
    + '<button class="vessel-window-close" type="button" aria-label="关闭' + config.title + '">×</button></header>'
    + '<div class="vessel-window-body"><div class="vessel-chart-meta"></div><div class="vessel-chart-guide"></div>'
    + '<div class="vessel-chart-stage"><canvas role="img" aria-label="' + config.title + '时间曲线"></canvas></div></div>';
  $("vesselOverlays").append(panel);
  placeVesselChart(panel);
  enableVesselDrag(panel);
  panel.querySelector("button").addEventListener("click", closeVesselChart);
  bringVesselWindowForward(panel);
  const chart = renderVesselChart(panel, result, field, config);
  vesselHistory.chartWindow = { field, panel, chart };
}

function placeVesselChart(panel) {
  const overlay = $("vesselOverlays");
  const card = $("vesselCard");
  const gap = 12;
  const leftSpace = card.offsetLeft - gap - 8;
  const rightEdge = card.offsetLeft + card.offsetWidth + gap;
  const rightSpace = overlay.clientWidth - rightEdge - 8;
  const sideSpace = Math.max(leftSpace, rightSpace);
  if (sideSpace >= 420) {
    panel.style.width = Math.min(680, sideSpace) + "px";
    const x = leftSpace >= rightSpace ? card.offsetLeft - gap - panel.offsetWidth : rightEdge;
    placeVesselWindow(panel, x, card.offsetTop);
  } else {
    panel.style.width = "";
    placeVesselWindow(panel, 12, card.offsetTop + card.offsetHeight + gap);
  }
}

function vesselChartPoints(points, field) {
  const data = [];
  let previous = null;
  for (const point of points) {
    const x = point.timestamp * 1000;
    const y = point[field];
    if (previous && (point.run !== previous.run || (
      field !== "speed" && y != null && previous[field] != null && Math.abs(y - previous[field]) > 180
    ))) data.push({ x, y: null });
    data.push({ x, y, point });
    previous = point;
  }
  return data;
}

function renderVesselChart(panel, result, field, config) {
  const speed = field === "speed";
  const meta = panel.querySelector(".vessel-chart-meta");
  const guide = panel.querySelector(".vessel-chart-guide");
  const canvas = panel.querySelector("canvas");
  const data = vesselChartPoints(result.points, field);
  const valid = data.some(({ y }) => y != null);
  if (!valid) {
    meta.textContent = "该时段没有有效的" + config.name + "数据。";
    panel.querySelector(".vessel-chart-stage").hidden = true;
    return null;
  }

  const elapsed = result.points.at(-1).timestamp - result.points[0].timestamp;
  const sameDay = vesselDate(result.points[0].timestamp).slice(0, 10) === vesselDate(result.points.at(-1).timestamp).slice(0, 10);
  const hours = Math.floor(elapsed / 3600);
  const minutes = Math.floor(elapsed % 3600 / 60);
  const duration = (hours ? hours + " 小时 " : "") + minutes + " 分 " + elapsed % 60 + " 秒";
  const stats = speed ? "平均航速 " + vesselMeasure(result.speed_avg, 2, " kn") + " · 轨迹时长 " + duration : "";
  const sampled = result.points.length < result.point_count
    ? "显示 " + numberFormat.format(result.points.length) + " / " + numberFormat.format(result.point_count) + " 点"
    : "";
  meta.textContent = [stats, sampled, "点击数据点定位地图"].filter(Boolean).join(" · ");

  let threshold = 3;
  let ignoreClickUntil = 0;
  const low = "#2465d6";
  const high = "#d73751";
  const speedColor = (value) => value > threshold ? high : low;
  const guideLine = {
    id: "vesselSpeedThreshold",
    afterDatasetsDraw(chart) {
      const { ctx, chartArea, scales } = chart;
      const y = scales.y.getPixelForValue(threshold);
      ctx.save();
      ctx.beginPath();
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = "#53677d";
      ctx.lineWidth = 1.5;
      ctx.moveTo(chartArea.left, y);
      ctx.lineTo(chartArea.right, y);
      ctx.stroke();
      ctx.restore();
    },
  };
  const chart = new Chart(canvas, {
    type: "line",
    data: { datasets: [{
      data,
      label: config.name,
      borderColor: ({ chart }) => {
        if (!speed || !chart.chartArea) return config.color;
        const { top, bottom } = chart.chartArea;
        if (bottom <= top) return low;
        const cut = Math.max(0, Math.min(1, (chart.scales.y.getPixelForValue(threshold) - top) / (bottom - top)));
        const gradient = chart.ctx.createLinearGradient(0, top, 0, bottom);
        gradient.addColorStop(0, high);
        gradient.addColorStop(cut, high);
        gradient.addColorStop(cut, low);
        gradient.addColorStop(1, low);
        return gradient;
      },
      pointBackgroundColor: ({ raw }) => speed ? speedColor(raw?.y) : config.color,
      pointBorderColor: ({ raw }) => speed ? speedColor(raw?.y) : config.color,
    }] },
    plugins: speed ? [guideLine] : [],
    options: {
      animation: false, parsing: false, maintainAspectRatio: false, spanGaps: false,
      elements: { line: { borderWidth: 1.8 }, point: { radius: data.length > 800 ? 0 : 2, hoverRadius: 5, hitRadius: 7 } },
      interaction: { mode: "nearest", intersect: false, axis: "xy" },
      onClick: (_event, items) => {
        if (performance.now() < ignoreClickUntil) return;
        const point = data[items[0]?.index]?.point;
        if (point) locateVesselPoint(point);
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: {
          title: ([item]) => vesselDate(item.parsed.x / 1000),
          label: (item) => config.name + " " + vesselMeasure(item.parsed.y, 2, config.unit),
        } },
      },
      scales: {
        x: { type: "linear", title: { display: true, text: "时间（北京时间）" },
          ticks: { maxTicksLimit: 6, maxRotation: 0, callback: (value) => {
            const stamp = vesselDate(value / 1000);
            return sameDay ? stamp.slice(11, elapsed < 600 ? 19 : 16) : stamp.slice(5, 16);
          } } },
        y: { min: 0, ...(speed ? { suggestedMax: 4 } : { max: 360 }),
          title: { display: true, text: config.name + (speed ? "（kn）" : "（°）") },
          ticks: speed ? { maxTicksLimit: 7 } : { stepSize: 90 } },
      },
    },
  });

  if (speed) {
    guide.innerHTML = '<span class="vessel-speed-high">● 高速</span><span class="vessel-speed-low">● 低速</span>'
      + '<span class="vessel-threshold-readout"></span><span>上下拖动虚线调整阈值</span>';
    const readout = guide.querySelector(".vessel-threshold-readout");
    const updateReadout = () => { readout.textContent = "阈值 " + formatNumber(threshold, 1) + " kn"; };
    updateReadout();
    const setThreshold = (value) => {
      threshold = Math.max(chart.scales.y.min, Math.min(chart.scales.y.max, Math.round(value * 10) / 10));
      updateReadout();
      chart.update("none");
    };
    let dragging = false;
    const position = (event) => {
      const box = canvas.getBoundingClientRect();
      return [event.clientX - box.left, event.clientY - box.top];
    };
    const nearLine = (x, y) => x >= chart.chartArea.left && x <= chart.chartArea.right
      && Math.abs(y - chart.scales.y.getPixelForValue(threshold)) <= 9;
    canvas.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || !nearLine(...position(event))) return;
      dragging = true;
      canvas.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    canvas.addEventListener("pointermove", (event) => {
      const [x, y] = position(event);
      canvas.style.cursor = dragging || nearLine(x, y) ? "ns-resize" : "crosshair";
      if (dragging) setThreshold(chart.scales.y.getValueForPixel(y));
    });
    canvas.addEventListener("lostpointercapture", () => {
      if (dragging) ignoreClickUntil = performance.now() + 250;
      dragging = false;
    });
    canvas.tabIndex = 0;
    canvas.addEventListener("keydown", (event) => {
      if (!["ArrowUp", "ArrowDown"].includes(event.key)) return;
      event.preventDefault();
      setThreshold(threshold + (event.key === "ArrowUp" ? 0.1 : -0.1));
    });
  }
  return chart;
}
