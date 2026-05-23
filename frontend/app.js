const $ = (id) => document.getElementById(id);

const state = {
  filters: {},
  statusTimer: null,
  map: null,
  mapLoaded: false,
  mapFitted: false,
  manifest: null,
  activeKey: "",
  mapHandlersBound: false,
};

const numberFormat = new Intl.NumberFormat("zh-CN");

window.addEventListener("DOMContentLoaded", () => {
  $("buildBtn").addEventListener("click", startBuild);
  $("refreshBtn").addEventListener("click", loadAll);
  $("runCleanBtn").addEventListener("click", runCleaning);
  $("resetCleanBtn").addEventListener("click", resetCleaning);
  $("applyBtn").addEventListener("click", () => {
    state.filters = collectFilters();
    loadAll();
    refreshMapTiles();
  });
  $("clearBtn").addEventListener("click", clearFilters);
  $("densityBtn").addEventListener("click", refreshDensity);
  $("excelBtn").addEventListener("click", exportExcel);
  $("grainSelect").addEventListener("change", loadTimeSeries);
  $("trackBtn").addEventListener("click", loadTrack);
  $("fitMapBtn").addEventListener("click", fitMapToData);
  $("reloadTilesBtn").addEventListener("click", refreshMapTiles);
  $("reportExcelBtn").addEventListener("click", exportExcel);
  $("reportDensityBtn").addEventListener("click", refreshDensity);
  $("reportRefreshBtn").addEventListener("click", loadAll);
  initMap();
  pollStatus();
  state.statusTimer = setInterval(pollStatus, 2500);
});

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

async function postJson(path, payload = null) {
  const options = { method: "POST" };
  if (payload) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(payload);
  }
  const response = await fetch(path, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

async function pollStatus() {
  try {
    const data = await api("/api/status");
    $("statusText").textContent = data.cleaned_dataset_ready
      ? "清洗数据可用"
      : data.dataset_ready
        ? "原始数据可用"
        : "等待构建";

    const job = data.build_job || {};
    $("jobText").textContent = formatJob(job, job.progress || {});

    const cleanJob = data.clean_job || {};
    $("cleanJobText").textContent = formatCleanJob(cleanJob, cleanJob.progress || {}, data.cleaning_manifest);
    updateCleanProgress(cleanJob, cleanJob.progress || {}, data.cleaning_manifest);
    renderCleaningSummary(data.cleaning_manifest);

    const activeManifest = data.active_manifest || data.manifest;
    if (activeManifest) {
      const activeKey = `${activeManifest.data_role || "source"}:${activeManifest.created_at || ""}:${activeManifest.row_count || 0}`;
      const activeChanged = state.activeKey && state.activeKey !== activeKey;
      state.activeKey = activeKey;
      state.manifest = activeManifest;

      const role = activeManifest.data_role === "cleaned" ? "清洗数据" : "原始数据";
      $("datasetMeta").textContent = `${role}：${numberFormat.format(activeManifest.row_count || 0)} 点 / ${numberFormat.format(activeManifest.trajectory_count || 0)} 条轨迹`;

      if (activeChanged) {
        state.mapFitted = false;
        loadAll();
        refreshMapTiles();
      }
      fitMapToDataOnce();
    }

    if (data.dataset_ready && !$("metrics").children.length) {
      loadAll();
    }
  } catch (error) {
    $("statusText").textContent = "服务未就绪";
    $("jobText").textContent = error.message;
    $("cleanJobText").textContent = error.message;
  }
}

function formatJob(job, progress) {
  if (!job.status || job.status === "idle") return "待命";
  if (job.status === "running") {
    const rows = progress.processed_rows ? numberFormat.format(progress.processed_rows) : "0";
    const traj = progress.processed_trajectories && progress.total_trajectories
      ? `${numberFormat.format(progress.processed_trajectories)}/${numberFormat.format(progress.total_trajectories)}`
      : "";
    return `构建中 ${rows} 点 ${traj}`;
  }
  return job.message || job.status;
}

function formatCleanJob(job, progress, manifest) {
  if (job.status === "running") return progress.message || job.message || "清洗中";
  if (job.status === "error") return job.message || "清洗失败";
  if (manifest && manifest.counts) {
    return `已清洗，剔除 ${numberFormat.format(manifest.counts.removed_rows || 0)} 点`;
  }
  return "待命";
}

function updateCleanProgress(job, progress, manifest) {
  const fill = $("cleanProgressFill");
  const value = $("cleanProgressValue");
  const stage = $("cleanProgressStage");
  if (!fill || !value || !stage) return;

  let percent = Number(progress.percent);
  let text = progress.message || "等待清洗任务";

  if (job.status === "done" || (manifest && manifest.counts && job.status !== "running")) {
    percent = 100;
    text = "清洗完成";
  } else if (job.status === "error") {
    percent = 0;
    text = progress.message || job.message || "清洗失败";
  } else if (!Number.isFinite(percent)) {
    percent = job.status === "running" ? 5 : 0;
  }

  percent = Math.max(0, Math.min(100, percent));
  fill.style.width = `${percent}%`;
  fill.style.background = job.status === "error" ? "var(--red)" : "var(--blue)";
  value.textContent = `${formatNumber(percent, percent % 1 ? 1 : 0)}%`;
  stage.textContent = text;
}

async function startBuild() {
  $("jobText").textContent = "提交任务中";
  await fetch("/api/build", { method: "POST" });
  pollStatus();
}

async function runCleaning() {
  $("cleanJobText").textContent = "提交清洗任务中";
  updateCleanProgress({ status: "running" }, { stage: "queued", message: "提交清洗任务中", percent: 1 }, null);
  await postJson("/api/cleaning/run", collectCleaningConfig());
  pollStatus();
}

async function resetCleaning() {
  $("cleanJobText").textContent = "切换回原始数据";
  updateCleanProgress({ status: "idle" }, { stage: "idle", message: "等待清洗任务", percent: 0 }, null);
  await postJson("/api/cleaning/reset");
  state.activeKey = "";
  state.mapFitted = false;
  await pollStatus();
  loadAll();
  refreshMapTiles();
}

async function loadAll() {
  await Promise.allSettled([
    loadSummary(),
    loadSpeedHistogram(),
    loadShipTypes(),
    loadNavStates(),
    loadTimeSeries(),
    refreshDensity(),
  ]);
}

async function loadSummary() {
  const data = await api(`/api/summary${queryString()}`);
  renderMetrics([
    ["轨迹点", numberFormat.format(data.point_count || 0)],
    ["船舶数", numberFormat.format(data.vessel_count || 0)],
    ["平均航速", formatNumber(data.speed_avg, 2)],
    ["轨迹数", numberFormat.format(data.trajectory_count || 0)],
    ["最小时间", formatTime(data.timestamp_min)],
    ["最大时间", formatTime(data.timestamp_max)],
    ["经度范围", `${formatNumber(data.lon_min, 3)} / ${formatNumber(data.lon_max, 3)}`],
    ["纬度范围", `${formatNumber(data.lat_min, 3)} / ${formatNumber(data.lat_max, 3)}`],
  ]);
}

function renderMetrics(items) {
  $("metrics").innerHTML = items
    .map(([label, value]) => `<article class="metric"><div class="label">${label}</div><div class="value">${value}</div></article>`)
    .join("");
}

function renderCleaningSummary(manifest) {
  if (!manifest || !manifest.counts) {
    $("cleaningSummary").innerHTML = "";
    $("qualityPanel").innerHTML = "";
    return;
  }
  const counts = manifest.counts;
  const items = [
    ["清洗输入", numberFormat.format(counts.raw_rows || 0)],
    ["清洗输出", numberFormat.format(counts.output_rows || 0)],
    ["剔除点", numberFormat.format(counts.removed_rows || 0)],
    ["重复点", numberFormat.format(counts.duplicate_rows || 0)],
    ["跳点", numberFormat.format(counts.jump_point_rows || 0)],
    ["加速度异常", numberFormat.format(counts.acceleration_point_rows || 0)],
    ["断点数", numberFormat.format(counts.segment_break_rows || 0)],
    ["时间分段", numberFormat.format(counts.time_segment_break_rows || 0)],
    ["距离分段", numberFormat.format(counts.distance_segment_break_rows || 0)],
    ["耗时", `${formatNumber(manifest.elapsed_seconds, 1)} 秒`],
  ];
  $("cleaningSummary").innerHTML = items
    .map(([label, value]) => `<article class="metric"><div class="label">${label}</div><div class="value">${value}</div></article>`)
    .join("");
  renderQualityPanel(counts);
}

async function loadSpeedHistogram() {
  const data = await api(`/api/speed-histogram${queryString({ bins: 48 })}`);
  drawBars($("speedCanvas"), data.counts || [], data.bins || [], "速度", "#1769aa");
}

async function loadShipTypes() {
  const data = await api(`/api/ship-types${queryString({ limit: 18 })}`);
  drawBars(
    $("shipCanvas"),
    data.map((item) => item.point_count),
    data.map((item) => String(item.ship_type)),
    "船型",
    "#2b8a6e"
  );
}

async function loadNavStates() {
  const data = await api(`/api/nav-states${queryString()}`);
  renderNavStates(data);
}

function renderNavStates(rows) {
  const panel = $("navStatePanel");
  if (!panel) return;
  if (!rows || !rows.length) {
    panel.innerHTML = `<div class="muted">暂无航行状态数据</div>`;
    return;
  }
  const colors = {
    underway: "#1d6fb8",
    low_speed: "#d47b1f",
    stopped: "#14907f",
  };
  panel.innerHTML = rows
    .map((row) => {
      const pct = Math.max(0, Math.min(100, row.percentage || 0));
      return `
        <div>
          <div class="state-bar-label"><span>${row.label || row.state}</span><strong>${formatNumber(pct, 1)}%</strong></div>
          <div class="progress-track"><div class="progress-fill" style="width:${pct}%;background:${colors[row.state] || "#64748b"}"></div></div>
          <div class="muted">${numberFormat.format(row.point_count || 0)} 点</div>
        </div>
      `;
    })
    .join("");
}

function renderQualityPanel(counts) {
  const panel = $("qualityPanel");
  if (!panel) return;
  const raw = Math.max(Number(counts.raw_rows || 0), 1);
  const items = [
    ["重复数据", counts.duplicate_rows || 0, "已处理"],
    ["航向/字段异常", (counts.invalid_course_rows || 0) + (counts.invalid_position_rows || 0) + (counts.invalid_speed_rows || 0), "已过滤"],
    ["跳点/漂移", counts.jump_point_rows || 0, "已识别"],
    ["速度突变点", counts.acceleration_point_rows || 0, "已剔除"],
  ];
  panel.innerHTML = items
    .map(([label, value, status]) => {
      const pct = (Number(value) / raw) * 100;
      return `
        <div class="quality-item">
          <div class="quality-row"><span>${label}</span><strong>${formatNumber(pct, 2)}%</strong></div>
          <div class="progress-track"><div class="progress-fill" style="width:${Math.min(100, pct * 12)}%"></div></div>
          <div class="muted">${status} · ${numberFormat.format(value)} 点</div>
        </div>
      `;
    })
    .join("");
}

async function loadTimeSeries() {
  const grain = $("grainSelect").value;
  const data = await api(`/api/time-series${queryString({ grain })}`);
  drawLine(
    $("timeCanvas"),
    data.map((item) => item.point_count),
    data.map((item) => item.bucket),
    "#c97922"
  );
}

function refreshDensity() {
  const bins = $("densityBinsInput").value || "400";
  $("densityImage").src = `/api/density.png${queryString({ bins, _: Date.now() })}`;
}

async function loadTrack() {
  const mmsi = $("trackMmsiInput").value.trim();
  if (!mmsi) return;
  const data = await api(`/api/track/${encodeURIComponent(mmsi)}?max_points=50000`);
  drawTrack($("trackCanvas"), data.points || []);
  $("trackMeta").textContent = `${mmsi}: ${numberFormat.format(data.sampled_points)} / ${numberFormat.format(data.total_points)} 点`;
}

function initMap() {
  if (!window.maplibregl) {
    $("mapStatus").textContent = "MapLibre GL JS 未加载，检查网络或改成本地资源。";
    return;
  }

  state.map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      sources: {
        osm: {
          type: "raster",
          tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
          tileSize: 256,
          attribution: "OpenStreetMap contributors",
        },
      },
      layers: [
        {
          id: "osm",
          type: "raster",
          source: "osm",
        },
      ],
    },
    center: [110.224, 20.18],
    zoom: 10,
    minZoom: 7,
    maxZoom: 17,
  });
  state.map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
  state.map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
  state.map.on("load", () => {
    state.mapLoaded = true;
    $("mapStatus").textContent = "轨迹瓦片加载中";
    refreshMapTiles();
    fitMapToDataOnce();
  });
  state.map.on("error", (event) => {
    if (event && event.error) {
      $("mapStatus").textContent = event.error.message || "地图加载出现错误";
    }
  });
}

function refreshMapTiles() {
  const map = state.map;
  if (!map || !state.mapLoaded) return;

  const sourceId = "ais-tracks";
  const lineLayerId = "ais-track-lines";
  const outlineLayerId = "ais-track-outline";
  if (map.getLayer(lineLayerId)) map.removeLayer(lineLayerId);
  if (map.getLayer(outlineLayerId)) map.removeLayer(outlineLayerId);
  if (map.getSource(sourceId)) map.removeSource(sourceId);

  const tileUrl = `${location.origin}/api/tiles/tracks/{z}/{x}/{y}.mvt${queryString({
    max_points: 30000,
    _v: Date.now(),
  })}`;

  map.addSource(sourceId, {
    type: "vector",
    tiles: [tileUrl],
    minzoom: 8,
    maxzoom: 16,
  });

  map.addLayer({
    id: outlineLayerId,
    type: "line",
    source: sourceId,
    "source-layer": "tracks",
    paint: {
      "line-color": "#ffffff",
      "line-opacity": 0.75,
      "line-width": ["interpolate", ["linear"], ["zoom"], 8, 1.8, 13, 3.5, 16, 5],
    },
  });

  map.addLayer({
    id: lineLayerId,
    type: "line",
    source: sourceId,
    "source-layer": "tracks",
    paint: {
      "line-color": [
        "match",
        ["get", "ship_type"],
        60,
        "#1769aa",
        70,
        "#2b8a6e",
        30,
        "#c97922",
        90,
        "#b94d42",
        "#26384a",
      ],
      "line-opacity": 0.82,
      "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.8, 13, 2.2, 16, 4],
    },
  });

  bindTrackMapHandlers();
  $("mapStatus").textContent = "轨迹瓦片已刷新";
}

function bindTrackMapHandlers() {
  if (state.mapHandlersBound || !state.map) return;
  state.mapHandlersBound = true;
  state.map.on("mouseenter", "ais-track-lines", () => {
    state.map.getCanvas().style.cursor = "pointer";
  });
  state.map.on("mouseleave", "ais-track-lines", () => {
    state.map.getCanvas().style.cursor = "";
  });
  state.map.on("click", "ais-track-lines", (event) => {
    const feature = event.features && event.features[0];
    if (!feature) return;
    const props = feature.properties || {};
    new maplibregl.Popup()
      .setLngLat(event.lngLat)
      .setHTML(
        `<strong>MMSI ${props.mmsi || "-"}</strong><br>` +
          `轨迹 ${props.trajectory_id || "-"}<br>` +
          `船型 ${props.ship_type ?? "-"}<br>` +
          `抽稀步长 ${props.step || "-"}`
      )
      .addTo(state.map);
  });
}

function fitMapToDataOnce() {
  if (state.mapFitted) return;
  fitMapToData();
}

function fitMapToData() {
  if (!state.map || !state.mapLoaded || !state.manifest || !state.manifest.bounds) return;
  const bounds = state.manifest.bounds;
  state.map.fitBounds(
    [
      [bounds.lon_min, bounds.lat_min],
      [bounds.lon_max, bounds.lat_max],
    ],
    { padding: 48, maxZoom: 12, duration: 700 }
  );
  state.mapFitted = true;
}

function collectFilters() {
  const fields = {
    start_ts: datetimeToTs($("startInput").value),
    end_ts: datetimeToTs($("endInput").value),
    lon_min: valueOrNull("lonMinInput"),
    lon_max: valueOrNull("lonMaxInput"),
    lat_min: valueOrNull("latMinInput"),
    lat_max: valueOrNull("latMaxInput"),
    ship_type: valueOrNull("shipTypeInput"),
    mmsi: valueOrNull("mmsiFilterInput"),
  };
  return Object.fromEntries(Object.entries(fields).filter(([, value]) => value !== null && value !== ""));
}

function collectCleaningConfig() {
  return {
    speed_max: valueOrNull("cleanSpeedMaxInput") ?? 50,
    max_jump_speed_knots: valueOrNull("cleanJumpSpeedInput") ?? 50,
    max_acceleration_knots_per_min: valueOrNull("cleanAccelInput") ?? 8,
    max_time_gap_minutes: valueOrNull("cleanGapInput") ?? 60,
    max_segment_distance_nm: valueOrNull("cleanDistanceGapInput") ?? 3,
    stop_speed_knots: valueOrNull("cleanStopSpeedInput") ?? 0.5,
    low_speed_knots: valueOrNull("cleanLowSpeedInput") ?? 3,
    remove_exact_duplicates: $("cleanDedupeInput").checked,
  };
}

function clearFilters() {
  ["startInput", "endInput", "lonMinInput", "lonMaxInput", "latMinInput", "latMaxInput", "shipTypeInput", "mmsiFilterInput"].forEach((id) => {
    $(id).value = "";
  });
  state.filters = {};
  loadAll();
  refreshMapTiles();
}

function queryString(extra = {}) {
  const params = new URLSearchParams();
  Object.entries({ ...state.filters, ...extra }).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") params.set(key, value);
  });
  const text = params.toString();
  return text ? `?${text}` : "";
}

function valueOrNull(id) {
  const value = $(id).value.trim();
  return value === "" ? null : Number(value);
}

function datetimeToTs(value) {
  if (!value) return null;
  return Math.floor(new Date(value).getTime() / 1000);
}

function formatNumber(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function formatTime(ts) {
  if (!ts) return "-";
  return new Date(Number(ts) * 1000).toLocaleString("zh-CN");
}

function drawBars(canvas, values, labels, axisLabel, color) {
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);
  const margin = { left: 54, right: 18, top: 24, bottom: 42 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const max = Math.max(...values, 1);
  const barWidth = plotWidth / Math.max(values.length, 1);
  ctx.strokeStyle = "#d8e0e8";
  ctx.strokeRect(margin.left, margin.top, plotWidth, plotHeight);
  ctx.fillStyle = color;
  values.forEach((value, index) => {
    const h = (value / max) * plotHeight;
    const x = margin.left + index * barWidth + 1;
    const y = margin.top + plotHeight - h;
    ctx.fillRect(x, y, Math.max(barWidth - 2, 1), h);
  });
  ctx.fillStyle = "#667381";
  ctx.font = "13px Segoe UI";
  ctx.fillText(axisLabel, margin.left, height - 12);
  ctx.fillText(numberFormat.format(max), 8, margin.top + 8);
  if (labels.length) {
    ctx.fillText(String(labels[0]).slice(0, 12), margin.left, height - 24);
    ctx.fillText(String(labels[labels.length - 1]).slice(0, 12), width - 110, height - 24);
  }
}

function drawLine(canvas, values, labels, color) {
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);
  const margin = { left: 62, right: 18, top: 24, bottom: 42 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const max = Math.max(...values, 1);
  ctx.strokeStyle = "#d8e0e8";
  ctx.strokeRect(margin.left, margin.top, plotWidth, plotHeight);
  ctx.beginPath();
  values.forEach((value, index) => {
    const x = margin.left + (index / Math.max(values.length - 1, 1)) * plotWidth;
    const y = margin.top + plotHeight - (value / max) * plotHeight;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.fillStyle = "#667381";
  ctx.font = "13px Segoe UI";
  ctx.fillText(numberFormat.format(max), 8, margin.top + 8);
  if (labels.length) {
    ctx.fillText(labels[0], margin.left, height - 16);
    ctx.fillText(labels[labels.length - 1], width - 120, height - 16);
  }
}

function drawTrack(canvas, points) {
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);
  if (!points.length) return;
  const lonValues = points.map((p) => p.lon);
  const latValues = points.map((p) => p.lat);
  const lonMin = Math.min(...lonValues);
  const lonMax = Math.max(...lonValues);
  const latMin = Math.min(...latValues);
  const latMax = Math.max(...latValues);
  const pad = 32;
  const project = (point) => {
    const x = pad + ((point.lon - lonMin) / Math.max(lonMax - lonMin, 1e-9)) * (width - pad * 2);
    const y = height - pad - ((point.lat - latMin) / Math.max(latMax - latMin, 1e-9)) * (height - pad * 2);
    return [x, y];
  };
  ctx.strokeStyle = "#d8e0e8";
  ctx.strokeRect(pad, pad, width - pad * 2, height - pad * 2);
  ctx.beginPath();
  points.forEach((point, index) => {
    const [x, y] = project(point);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = "#1769aa";
  ctx.lineWidth = 2;
  ctx.stroke();
  const [sx, sy] = project(points[0]);
  const [ex, ey] = project(points[points.length - 1]);
  ctx.fillStyle = "#2b8a6e";
  ctx.beginPath();
  ctx.arc(sx, sy, 5, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#b94d42";
  ctx.beginPath();
  ctx.arc(ex, ey, 5, 0, Math.PI * 2);
  ctx.fill();
}

function exportExcel() {
  window.location.href = `/api/export/excel${queryString()}`;
}
