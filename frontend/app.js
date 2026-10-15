const $ = (id) => document.getElementById(id);

const state = {
  filters: {},
  statusTimer: null,
  map: null,
  mapLoaded: false,
  mapFitted: false,
  manifest: null,
  activeKey: "",
  activeDatasetId: "",
  historyDatasets: [],
  statusRequestId: 0,
  markers: [],
  markerType: "point",
  areaShape: "polygon",
  openMapTool: null,
  drawing: false,
  draftPoints: [],
  draftCursor: null,
  editingMarkerId: null,
  measuring: false,
  measurePoints: [],
  spatialFilter: null,
  behaviorFilter: null,
  coordinateFormat: "decimal",
  cursorLngLat: null,
};

const numberFormat = new Intl.NumberFormat("zh-CN");
let speedChart = null;
let shipChart = null;
let timeChart = null;
let lengthChart = null;
let courseDirectionChart = null;
let headingDirectionChart = null;
let directionTimeChart = null;
let directionRequestId = 0;
let directionTimeRequestId = 0;

function shipTypeName(code) {
  const n = Number(code);
  if (n === -1 || !Number.isFinite(n)) return "未知类型";
  if (n >= 20 && n <= 29) return "翄地船";
  if (n === 30) return "渔船";
  if (n === 31 || n === 32) return "拖轮";
  if (n === 33) return "疏浚船";
  if (n === 34) return "潜水作业";
  if (n === 35) return "军事";
  if (n === 36) return "帆船";
  if (n === 37) return "游艇";
  if (n >= 40 && n <= 49) return "高速船";
  if (n === 50) return "引航船";
  if (n === 51) return "搜救船";
  if (n === 52) return "拖轮";
  if (n === 53) return "港务船";
  if (n === 54) return "防污染船";
  if (n === 55) return "执法船";
  if (n === 58) return "医疗船";
  if (n === 59) return "不受限船";
  if (n >= 60 && n <= 69) return "客船";
  if (n >= 70 && n <= 79) return "货船";
  if (n >= 80 && n <= 89) return "油轮";
  if (n >= 90 && n <= 99) return "其他";
  return `类型${code}`;
}

function setupAccountMenu() {
  const wrapper = $("accountMenuWrap");
  const button = $("accountMenuBtn");
  const menu = $("accountMenu");
  const items = () => [...menu.querySelectorAll("a:not([hidden]), button:not([hidden])")];
  const setOpen = (open) => {
    menu.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
  };

  button.disabled = false;
  button.addEventListener("click", () => setOpen(menu.hidden));
  button.addEventListener("keydown", (event) => {
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    event.preventDefault();
    setOpen(true);
    const entries = items();
    (event.key === "ArrowDown" ? entries[0] : entries.at(-1))?.focus();
  });
  menu.addEventListener("click", (event) => {
    if (event.target.closest("a, button")) setOpen(false);
  });
  menu.addEventListener("keydown", (event) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const entries = items();
    const current = entries.indexOf(document.activeElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? entries.length - 1
      : (current + (event.key === "ArrowDown" ? 1 : -1) + entries.length) % entries.length;
    entries[next]?.focus();
  });
  wrapper.addEventListener("focusout", (event) => {
    if (!wrapper.contains(event.relatedTarget)) setOpen(false);
  });
  document.addEventListener("click", (event) => {
    if (!wrapper.contains(event.target)) setOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !menu.hidden) {
      event.preventDefault();
      setOpen(false);
      button.focus();
    }
  });
}

function setupInfoPopover() {
  const button = $("resultsInfoButton");
  const popover = $("resultsInfoPopover");
  if (!button || !popover) return;

  const close = () => {
    popover.hidden = true;
    button.setAttribute("aria-expanded", "false");
  };

  button.addEventListener("click", (event) => {
    event.stopPropagation();
    const shouldOpen = popover.hidden;
    popover.hidden = !shouldOpen;
    button.setAttribute("aria-expanded", String(shouldOpen));
  });
  popover.addEventListener("click", (event) => event.stopPropagation());
  document.addEventListener("click", close);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !popover.hidden) {
      close();
      button.focus();
    }
  });
}

window.addEventListener("DOMContentLoaded", async () => {
  setupInfoPopover();
  try { await AIS.initialize(); } catch { return; }
  $("accountIdentity").textContent = AIS.user.username;
  $("accountMenuBtn").title = AIS.user.username;
  $("accountDisplayName").textContent = AIS.user.display_name || AIS.user.username;
  $("accountRole").textContent = AIS.user.role === "superadmin" ? "超级管理员" : "普通用户";
  $("adminEntry").hidden = AIS.user.role !== "superadmin";
  setupAccountMenu();
  $("logoutBtn").addEventListener("click", async () => {
    clearInterval(state.statusTimer);
    clearDatasetResults();
    state.manifest = null;
    state.activeDatasetId = "";
    state.activeKey = "";
    try { await AIS.logout(); } catch (error) { $("jobText").textContent = error.message; }
  });
  $("historyToggleBtn").addEventListener("click", () => {
    const panel = $("datasetHistoryPanel"); panel.hidden = !panel.hidden;
    $("historyToggleBtn").setAttribute("aria-expanded", String(!panel.hidden));
  });
  $("newDatasetBtn").addEventListener("click", () => {
    $("newDatasetDetails").open = true;
    $("datasetNameInput").focus();
  });
  $("datasetSearchInput").addEventListener("input", () => renderDatasets(state.historyDatasets, state.activeDatasetId));
  $("clearDatasetBtn").addEventListener("click", async () => {
    try { await postJson("/api/datasets/clear"); resetFilters(); await pollStatus(); }
    catch (error) { $("jobText").textContent = error.message; }
  });
  document.querySelectorAll("[data-collapse-card]").forEach((button) => {
    button.addEventListener("click", () => toggleSideCard(button));
  });
  $("buildBtn").addEventListener("click", startBuild);
  $("selectDatasetBtn").addEventListener("click", selectDataset);
  $("reloadDatasetsBtn").addEventListener("click", loadDatasets);
  $("uploadDatasetBtn").addEventListener("click", () => $("datasetFileInput").click());
  $("datasetFileInput").addEventListener("change", uploadDataset);
  $("refreshBtn").addEventListener("click", loadAll);
  $("runCleanBtn").addEventListener("click", runCleaning);
  $("resetCleanBtn").addEventListener("click", resetCleaning);
  $("applyBtn").addEventListener("click", () => {
    state.filters = collectFilters();
    loadAll();
    refreshTrackTiles();
  });
  $("clearBtn").addEventListener("click", clearFilters);
  $("grainSelect").addEventListener("change", loadTimeSeries);
  $("speedBinWidthSelect").addEventListener("change", loadSpeedHistogram);
  $("lengthBinWidthSelect").addEventListener("change", loadLengthHistogram);
  ["directionBinWidthSelect", "headingBinWidthSelect"].forEach((id) => {
    $(id).addEventListener("change", () => {
      const width = $(id).value;
      $("directionBinWidthSelect").value = width;
      $("headingBinWidthSelect").value = width;
      loadDirectionHistograms();
    });
  });
  $("applyDirectionMmsiBtn").addEventListener("click", applyDirectionVessel);
  $("directionMmsiInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") applyDirectionVessel();
  });
  $("mapFullscreenBtn").addEventListener("click", toggleMapFullscreen);
  $("fitMapBtn").addEventListener("click", fitMapToData);
  $("reloadTracksBtn").addEventListener("click", refreshTrackTiles);
  $("mapCoordinates").addEventListener("click", toggleCoordinateFormat);
  $("toggleMarkerPanelBtn").addEventListener("click", () => toggleMapTool("marker"));
  $("toggleFilterPanelBtn").addEventListener("click", () => toggleMapTool("filter"));
  $("toggleMeasurePanelBtn").addEventListener("click", () => toggleMapTool("measure"));
  $("toggleBehaviorPanelBtn").addEventListener("click", () => toggleMapTool("behavior"));
  $("applyBehaviorBtn").addEventListener("click", applyBehaviorFilter);
  $("clearBehaviorBtn").addEventListener("click", clearBehaviorFilter);
  $("undoMeasureBtn").addEventListener("click", undoMeasurePoint);
  $("clearMeasureBtn").addEventListener("click", clearMeasurement);
  $("beginMarkerBtn").addEventListener("click", beginMarkerDrawing);
  $("finishMarkerBtn").addEventListener("click", finishMarkerDrawing);
  $("cancelMarkerBtn").addEventListener("click", cancelMarkerDrawing);
  $("addCoordinateBtn").addEventListener("click", addCoordinatePoints);
  $("undoCoordinateBtn").addEventListener("click", undoCoordinatePoint);
  $("backToMarkerGroupsBtn").addEventListener("click", showMarkerOverview);
  document.querySelectorAll("[data-marker-add]").forEach((button) => {
    button.addEventListener("click", () => openMarkerEditor(button.dataset.markerAdd));
  });
  $("spatialModeSelect").addEventListener("change", updateSpatialMarkerOptions);
  $("applySpatialBtn").addEventListener("click", applySpatialFilter);
  $("clearSpatialBtn").addEventListener("click", clearSpatialFilter);
  document.querySelectorAll("[data-marker-type]").forEach((button) => {
    button.addEventListener("click", () => selectMarkerType(button.dataset.markerType));
  });
  document.querySelectorAll("[data-area-shape]").forEach((button) => {
    button.addEventListener("click", () => selectAreaShape(button.dataset.areaShape));
  });
  $("reportExcelBtn").addEventListener("click", exportExcel);
  $("reportRefreshBtn").addEventListener("click", loadAll);
  loadMarkers();
  initMap();
  pollStatus();
  state.statusTimer = setInterval(pollStatus, 2500);
  document.addEventListener("fullscreenchange", () => {
    $("mapFullscreenText").textContent = document.fullscreenElement ? "退出全屏" : "全屏查看";
    requestAnimationFrame(() => state.map?.resize());
  });
});

function toggleSideCard(button) {
  const card = button.closest(".side-card");
  const collapsed = card.classList.toggle("collapsed");
  button.setAttribute("aria-expanded", String(!collapsed));
  button.title = collapsed ? "展开" : "收起";
}

async function toggleMapFullscreen() {
  if (document.fullscreenElement) await document.exitFullscreen();
  else await document.querySelector(".map-card").requestFullscreen();
}

async function api(path) {
  const datasetId = state.activeDatasetId;
  const response = await authenticatedFetch(path);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === "string" ? error.detail : response.statusText);
  }
  const data = await response.json();
  if (datasetId !== state.activeDatasetId && !["/api/status", "/api/datasets"].includes(path)) throw new Error("数据已切换。");
  return data;
}

function authenticatedFetch(path, options) {
  return window.AIS ? window.AIS.request(path, options) : fetch(path, options);
}

async function postJson(path, payload = null) {
  const options = { method: "POST" };
  if (payload) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(payload);
  }
  const response = await authenticatedFetch(path, options);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === "string" ? error.detail : response.statusText);
  }
  return response.json();
}

async function deleteJson(path) {
  const response = await authenticatedFetch(path, { method: "DELETE" });
  if (!response.ok) throw new Error((await response.text()) || response.statusText);
  return response.json();
}

async function loadDatasets() {
  const data = await api("/api/datasets");
  renderDatasets(data.datasets || [], data.active_dataset_id || "");
  return data;
}

async function selectDataset() {
  const datasetId = $("datasetSelect").value;
  if (!datasetId || datasetId === state.activeDatasetId) return;
  $("jobText").textContent = "正在切换数据";
  try {
    await postJson(`/api/datasets/select/${encodeURIComponent(datasetId)}`);
    clearDatasetResults();
    state.activeKey = "";
    state.mapFitted = false;
    resetFilters();
    await pollStatus();
    loadAll();
    refreshTrackTiles();
  } catch (error) { $("jobText").textContent = error.message; }
}

function importOptions() {
  return {
    source_timezone: $("importTimezoneSelect").value,
    csv_encoding: $("importEncodingSelect").value,
  };
}

function renderImportMapping(data) {
  const panel = $("importMappingPanel");
  panel.hidden = false;
  if (data.format === "pkl") {
    panel.innerHTML = `<b>PKL 轨迹数据</b><small>${numberFormat.format(data.trajectory_count)} 条轨迹 · ${numberFormat.format(data.row_count)} 个轨迹点</small>`;
    return;
  }
  const entries = Object.entries(data.mapping || {});
  panel.innerHTML = `<b>识别结果</b><small>${escapeHtml(data.sheet || "")} · ${escapeHtml(data.source_timezone || "")}</small>`
    + entries.map(([field, column]) => `<div><span>${escapeHtml(column)}</span><span>→ ${escapeHtml(data.field_labels?.[field] || field)} (${escapeHtml(field)})</span></div>`).join("")
    + (data.warnings || []).map((warning) => `<p>${escapeHtml(warning)}</p>`).join("");
}

async function uploadDataset() {
  const file = $("datasetFileInput").files[0];
  if (!file) return;
  const button = $("uploadDatasetBtn");
  button.disabled = true;
  try {
    $("importMappingPanel").hidden = true;
    $("jobText").textContent = "正在上传并识别文件";
    const form = new FormData();
    form.append("file", file);
    form.append("name", $("datasetNameInput").value.trim());
    Object.entries(importOptions()).forEach(([key, value]) => form.append(key, value));
    const response = await authenticatedFetch("/api/datasets/upload", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "上传失败");
    renderImportMapping(data.preview);
    $("datasetNameInput").value = "";
    state.activeKey = "";
    state.mapFitted = false;
    state.filters = {};
    await pollStatus();
    $("jobText").textContent = "文件已添加，点击“加载数据”转换为可分析格式";
  } catch (error) { $("jobText").textContent = error.message; }
  finally { button.disabled = false; $("datasetFileInput").value = ""; }
}

async function pollStatus() {
  const requestId = ++state.statusRequestId;
  try {
    const data = await api("/api/status");
    if (requestId !== state.statusRequestId) return;
    if (state.activeDatasetId !== data.active_dataset_id) resetFilters();
    renderDatasets(data.datasets || [], data.active_dataset_id || "");
    const selectedDataset = (data.datasets || []).find(item => item.id === data.active_dataset_id);
    $("currentDatasetName").textContent = selectedDataset?.name || "尚未选择数据";
    $("emptyDatasetHint").hidden = Boolean(selectedDataset);
    $("clearDatasetBtn").hidden = !selectedDataset;
    $("buildBtn").disabled = !selectedDataset || data.build_job?.status === "running" || data.clean_job?.status === "running";
    ["runCleanBtn", "resetCleanBtn", "reportExcelBtn", "refreshBtn", "reportRefreshBtn", "applyBtn", "clearBtn",
      "applyDirectionMmsiBtn", "fitMapBtn", "reloadTracksBtn", "grainSelect", "speedBinWidthSelect", "lengthBinWidthSelect",
      "directionBinWidthSelect", "headingBinWidthSelect"].forEach(id => { $(id).disabled = !data.dataset_ready; });
    const job = !data.build_job?.dataset_id || data.build_job.dataset_id === data.active_dataset_id
      ? data.build_job || {} : {};
    $("jobText").textContent = formatJob(job, job.progress || {});

    const cleanJob = !data.clean_job?.dataset_id || data.clean_job.dataset_id === data.active_dataset_id
      ? data.clean_job || {} : {};
    $("cleanJobText").textContent = formatCleanJob(cleanJob, cleanJob.progress || {}, data.cleaning_manifest);
    updateCleanProgress(cleanJob, cleanJob.progress || {}, data.cleaning_manifest);
    renderCleaningSummary(data.cleaning_manifest);

    const activeManifest = data.active_manifest || data.manifest;
    if (activeManifest) {
      const activeKey = `${data.active_dataset_id || ""}:${activeManifest.data_role || "source"}:${activeManifest.created_at || ""}:${activeManifest.row_count || 0}`;
      const activeWasUnset = !state.activeKey;
      const activeChanged = state.activeKey && state.activeKey !== activeKey;
      state.activeKey = activeKey;
      state.activeDatasetId = data.active_dataset_id || "";
      state.manifest = activeManifest;

      const role = activeManifest.data_role === "cleaned" ? "清洗后数据" : "已加载数据";
      const datasetName = activeManifest.dataset_name ? `${activeManifest.dataset_name} · ` : "";
      $("datasetMeta").textContent = `${datasetName}${role}：${numberFormat.format(activeManifest.row_count || 0)} 点 / ${numberFormat.format(activeManifest.trajectory_count || 0)} 条轨迹`;

      if (activeChanged) {
        state.mapFitted = false;
        loadAll();
      }
      if ((activeWasUnset || activeChanged) && state.mapLoaded) refreshTrackTiles();
      fitMapToDataOnce();
    } else {
      state.activeDatasetId = data.active_dataset_id || "";
      state.manifest = null;
      state.activeKey = "";
      state.mapFitted = false;
      clearDatasetResults();
      const selected = (data.datasets || []).find((item) => item.id === data.active_dataset_id);
      $("datasetMeta").textContent = selected ? `${selected.name}：等待加载` : "等待加载数据";
    }

    if (data.dataset_ready && !$("metrics").children.length) {
      loadAll();
    }
  } catch (error) {
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
    return `正在加载：${rows} 条位置记录 ${traj}`;
  }
  if (job.status === "done") return "数据加载完成";
  if (job.status === "error") return "数据加载失败，请检查文件";
  return "待命";
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
  try {
    $("jobText").textContent = "正在开始加载";
    await postJson("/api/build");
    await pollStatus();
  } catch (error) { $("jobText").textContent = error.message; }
}

async function runCleaning() {
  try {
    $("cleanJobText").textContent = "提交清洗任务中";
    updateCleanProgress({ status: "running" }, { stage: "queued", message: "提交清洗任务中", percent: 1 }, null);
    await postJson("/api/cleaning/run", collectCleaningConfig());
    await pollStatus();
  } catch (error) {
    $("cleanJobText").textContent = error.message;
    updateCleanProgress({ status: "error", message: error.message }, { stage: "error", message: error.message }, null);
  }
}

async function resetCleaning() {
  try {
    $("cleanJobText").textContent = "切换回原始数据";
    updateCleanProgress({ status: "idle" }, { stage: "idle", message: "等待清洗任务", percent: 0 }, null);
    await postJson("/api/cleaning/reset");
    state.activeKey = "";
    state.mapFitted = false;
    await pollStatus();
    loadAll();
    refreshTrackTiles();
  } catch (error) { $("cleanJobText").textContent = error.message; }
}

async function loadAll() {
  if (!state.manifest) return;
  await Promise.allSettled([
    loadSummary(),
    loadSpeedHistogram(),
    loadShipTypes(),
    loadNavStates(),
    loadTimeSeries(),
    loadLengthHistogram(),
    loadDirectionHistograms(),
    loadDirectionTimeSeries(),
  ]);
}

function clearDatasetResults() {
  $("metrics").innerHTML = "";
  $("navStatePanel").innerHTML = '<div class="muted">等待加载数据</div>';
  [speedChart, shipChart, timeChart, lengthChart].forEach((chart) => chart?.destroy());
  speedChart = shipChart = timeChart = lengthChart = null;
  clearDirectionCharts();
  const map = state.map;
  clearDensityData();
  if (map?.getLayer("ais-tracks-raster")) map.removeLayer("ais-tracks-raster");
  if (map?.getSource("ais-tracks")) map.removeSource("ais-tracks");
  $("mapStatus").textContent = "等待加载数据";
}

async function loadSummary() {
  const data = await api(`/api/summary${queryString()}`);
  // 时间范围仅在首次加载时预填；经纬度保持为空，避免覆盖空间标记筛选。
  if (!$('startInput').value && data.timestamp_min) {
    $('startInput').value = tsToDatetimeLocal(data.timestamp_min);
  }
  if (!$('endInput').value && data.timestamp_max) {
    $('endInput').value = tsToDatetimeLocal(data.timestamp_max);
  }
  const sectionMode = data.spatial_mode === "section";
  renderMetrics([
    [sectionMode ? "截面穿越" : "轨迹点", numberFormat.format(data.point_count || 0)],
    ["船舶数", numberFormat.format(data.vessel_count || 0)],
    ["平均航速", formatNumber(data.speed_avg, 2)],
    ["轨迹数", numberFormat.format(data.trajectory_count || 0)],
    [sectionMode ? "正向穿越" : "最早时间", sectionMode ? numberFormat.format(data.forward_crossings || 0) : formatTime(data.timestamp_min)],
    [sectionMode ? "反向穿越" : "最晚时间", sectionMode ? numberFormat.format(data.reverse_crossings || 0) : formatTime(data.timestamp_max)],
  ]);
  renderSpatialTrafficStats(data);
}

function renderMetrics(items) {
  $("metrics").innerHTML = items
    .map(([label, value]) => `<article class="metric metric--summary${isWideOverviewMetric(label) ? " metric--wide" : ""}"><div class="label">${label}</div><div class="value">${value}</div></article>`)
    .join("");
}

function renderDatasets(datasets, activeDatasetId) {
  state.historyDatasets = datasets;
  $("historyCount").textContent = String(datasets.length);
  const select = $("datasetSelect");
  if (select) {
    const currentValue = select.value;
    select.innerHTML = '<option value="">请选择历史数据</option>' + datasets
      .map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}${item.active ? "（当前）" : ""}</option>`)
      .join("");
    select.value = datasets.some((item) => item.id === activeDatasetId) ? activeDatasetId : currentValue;
  }

  const list = $("datasetList");
  if (!list) return;
  if (!datasets.length) {
    list.innerHTML = `<div class="muted">暂无数据文件</div>`;
    return;
  }
  const search = $("datasetSearchInput").value.trim().toLowerCase();
  const matching = datasets.filter(item => `${item.name} ${item.filename || ""}`.toLowerCase().includes(search));
  if (!matching.length) { list.innerHTML = '<div class="muted">没有匹配的历史数据</div>'; return; }
  list.innerHTML = matching
    .map((item) => {
      const role = item.cleaned_dataset_ready ? "已清洗" : item.source_dataset_ready ? "已加载" : "待加载";
      const rows = item.row_count ? `${numberFormat.format(item.row_count)} 条位置记录` : "尚未加载";
      const source = item.source_exists ? "文件可用" : "找不到文件";
      return `
        <div class="dataset-item${item.active ? " active" : ""}">
          <strong>${escapeHtml(item.name)}</strong>
          <span class="history-filename">${escapeHtml(item.filename || "")}</span>
          <span>${role} · ${rows}${!item.source_exists ? ` · ${source}` : ""}</span>
          <span>导入：${AIS.formatTime(item.created_at)}</span>
          <span>最近使用：${AIS.formatTime(item.last_used_at)}</span>
          <div class="history-actions"><button class="btn ghost history-use" type="button" data-dataset-id="${escapeHtml(item.id)}" ${!item.source_exists && !item.source_dataset_ready ? "disabled" : ""}>${item.active ? "正在使用" : item.source_dataset_ready ? "使用此数据" : "加载此数据"}</button><button class="dataset-remove" type="button" data-dataset-id="${escapeHtml(item.id)}" data-dataset-name="${escapeHtml(item.name)}">移除记录</button></div>
        </div>
      `;
    })
    .join("");
  list.querySelectorAll(".dataset-remove").forEach((button) => {
    button.addEventListener("click", () => removeDataset(button.dataset.datasetId, button.dataset.datasetName));
  });
  list.querySelectorAll(".history-use").forEach(button => button.addEventListener("click", async () => {
    const item = datasets.find(dataset => dataset.id === button.dataset.datasetId);
    select.value = item.id;
    await selectDataset();
    if (state.activeDatasetId === item.id && !item.source_dataset_ready) await startBuild();
  }));
}

async function removeDataset(datasetId, datasetName) {
  if (!window.confirm(`从列表中移除“${datasetName}”？\n不会删除磁盘上的文件。`)) return;
  try {
    const data = await deleteJson(`/api/datasets/${encodeURIComponent(datasetId)}`);
    state.activeKey = "";
    renderDatasets(data.datasets || [], data.active_dataset_id || "");
    await pollStatus();
  } catch (error) {
    $("jobText").textContent = error.message;
  }
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
    .map(([label, value]) => `<article class="metric metric--clean${isWideOverviewMetric(label) ? " metric--wide" : ""}"><div class="label">${label}</div><div class="value">${value}</div></article>`)
    .join("");
  renderQualityPanel(counts);
}

function isWideOverviewMetric(label) {
  return ["最小时间", "最大时间", "经度范围", "纬度范围"].includes(label);
}

function clearDirectionCharts() {
  directionRequestId += 1;
  directionTimeRequestId += 1;
  [courseDirectionChart, headingDirectionChart, directionTimeChart].forEach((chart) => chart?.destroy());
  courseDirectionChart = headingDirectionChart = directionTimeChart = null;
  $("courseDirectionStats").innerHTML = "";
  $("headingDirectionStats").innerHTML = "";
  $("courseDirectionStatus").textContent = "等待加载数据";
  $("headingDirectionStatus").textContent = "等待加载数据";
  $("directionTimeStatus").textContent = "输入船舶编号后查看双曲线，并与当前时间、水域筛选联动。";
  $("directionMmsiInput").value = "";
}

function dominantDirectionLabel(item) {
  const intervals = item.dominant_bins || [];
  if (!intervals.length) return "无有效数据";
  if (intervals.length > 3) return `${intervals.length} 个区间并列`;
  return intervals.map((interval) => `${interval.start}–${interval.end}°`).join("、");
}

function renderDirectionHistogram(data, field, color) {
  const item = data[field];
  const name = field === "course" ? "对地航向（COG）" : "船首向（heading）";
  const mean = item.circular_mean === null
    ? (item.valid_count ? "方向分散" : "无有效数据")
    : `${formatNumber(item.circular_mean, 1)}°`;
  $(field === "course" ? "courseDirectionStats" : "headingDirectionStats").innerHTML = [
    ["有效点数", numberFormat.format(item.valid_count)],
    ["缺失/异常比例", `${formatNumber(item.unusable_percentage, 2)}%`],
    ["圆周平均方向", mean],
    ["主要方向区间", dominantDirectionLabel(item)],
  ].map(([label, value]) => `<div class="direction-stat"><span>${label}</span><b>${escapeHtml(value)}</b></div>`).join("");
  const status = $(field === "course" ? "courseDirectionStatus" : "headingDirectionStatus");
  status.textContent = !item.available ? "当前数据未提供 heading 船首向字段。"
    : !data.point_count ? "当前筛选范围没有 AIS 点。"
    : `缺失/不可用 ${numberFormat.format(item.missing_count)} 点，异常 ${numberFormat.format(item.invalid_count)} 点。`;
  return new Chart($(field === "course" ? "courseDirectionCanvas" : "headingDirectionCanvas"), {
    type: "bar",
    data: {
      labels: data.bins.slice(0, -1).map((start, index) => `${start}–${data.bins[index + 1]}°`),
      datasets: [{ label: name, data: item.counts, backgroundColor: color, borderRadius: 2 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (context) => [
          `${numberFormat.format(context.raw)} 个有效点`,
          `有效点占比 ${formatNumber(item.percentages[context.dataIndex], 2)}%`,
        ] } },
      },
      scales: {
        x: { title: { display: true, text: "方向区间（°，0° 为北）" }, ticks: { maxTicksLimit: 12 } },
        y: { beginAtZero: true, title: { display: true, text: "有效点数" } },
      },
    },
  });
}

async function loadDirectionHistograms() {
  if (!state.manifest) return;
  const requestId = ++directionRequestId;
  const activeKey = state.activeKey;
  $("courseDirectionStatus").textContent = $("headingDirectionStatus").textContent = "正在统计航向…";
  try {
    const data = await api(`/api/direction-histograms${queryString({ bin_width: $("directionBinWidthSelect").value })}`);
    if (requestId !== directionRequestId || activeKey !== state.activeKey) return;
    courseDirectionChart?.destroy();
    headingDirectionChart?.destroy();
    courseDirectionChart = renderDirectionHistogram(data, "course", "#ea580c");
    headingDirectionChart = renderDirectionHistogram(data, "heading", "#7c3aed");
  } catch (error) {
    if (requestId !== directionRequestId || activeKey !== state.activeKey) return;
    [courseDirectionChart, headingDirectionChart].forEach((chart) => chart?.destroy());
    courseDirectionChart = headingDirectionChart = null;
    $("courseDirectionStats").innerHTML = $("headingDirectionStats").innerHTML = "";
    $("courseDirectionStatus").textContent = $("headingDirectionStatus").textContent = "航向统计加载失败，请刷新统计后重试。";
    console.error("Direction histograms", error);
  }
}

function applyDirectionVessel() {
  const mmsi = $("directionMmsiInput").value.trim();
  if (!/^\d{9}$/.test(mmsi)) {
    $("directionTimeStatus").textContent = "请输入 9 位船舶编号（MMSI）。";
    return;
  }
  $("mmsiFilterInput").value = mmsi;
  state.filters = collectFilters();
  loadAll();
  refreshTrackTiles();
}

function directionLineData(points, field) {
  const data = [];
  for (const point of points) {
    const x = point.timestamp * 1000;
    if (data.length && point[`${field}_break`]) data.push({ x, y: null });
    data.push({ x, y: point[field] ?? null });
  }
  return data;
}

async function loadDirectionTimeSeries() {
  const requestId = ++directionTimeRequestId;
  const activeKey = state.activeKey;
  directionTimeChart?.destroy();
  directionTimeChart = null;
  $("directionMmsiInput").value = state.filters.mmsi ? String(state.filters.mmsi) : "";
  if (!state.filters.mmsi) {
    $("directionTimeStatus").textContent = "输入船舶编号后查看双曲线，并与当前时间、水域筛选联动。";
    return;
  }
  $("directionTimeStatus").textContent = "正在加载单船航向曲线…";
  try {
    const data = await api(`/api/direction-time-series${queryString()}`);
    if (requestId !== directionTimeRequestId || activeKey !== state.activeKey) return;
    if (!data.points.length) {
      $("directionTimeStatus").textContent = "该船舶在当前时间、水域及筛选条件下没有可展示的 AIS 点。";
      return;
    }
    $("directionTimeStatus").textContent = `MMSI ${data.mmsi} · 展示 ${numberFormat.format(data.displayed_points)} / ${numberFormat.format(data.total_points)} 点`
      + (data.sampled ? "；曲线已限量展示，分布统计与导出仍使用全部筛选点。" : "。")
      + (!data.heading_available ? "当前数据未提供船首向，仅显示对地航向。" : "");
    directionTimeChart = new Chart($("directionTimeCanvas"), {
      type: "line",
      data: { datasets: [
        { label: "对地航向（COG）", data: directionLineData(data.points, "course"), borderColor: "#ea580c", backgroundColor: "#ea580c" },
        { label: "船首向（heading）", data: directionLineData(data.points, "heading"), borderColor: "#7c3aed", backgroundColor: "#7c3aed" },
      ] },
      options: {
        responsive: true, maintainAspectRatio: false, parsing: false, spanGaps: false,
        elements: { line: { tension: 0, borderWidth: 1.8 }, point: { radius: data.points.length > 1000 ? 0 : 2, hoverRadius: 4 } },
        interaction: { mode: "nearest", axis: "x", intersect: false },
        plugins: { tooltip: { callbacks: {
          title: (items) => formatTime(items[0].parsed.x / 1000),
          label: (context) => `${context.dataset.label}：${formatNumber(context.parsed.y, 1)}°`,
        } } },
        scales: {
          x: { type: "linear", title: { display: true, text: "时间" }, ticks: { maxTicksLimit: 8, callback: (value) => new Date(Number(value)).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) } },
          y: { min: 0, max: 360, title: { display: true, text: "方向（°）" }, ticks: { stepSize: 90, callback: (value) => `${value}°` } },
        },
      },
    });
  } catch (error) {
    if (requestId !== directionTimeRequestId || activeKey !== state.activeKey) return;
    $("directionTimeStatus").textContent = "航向时间曲线加载失败，请重新选择船舶后重试。";
    console.error("Direction time series", error);
  }
}

async function loadSpeedHistogram() {
  const binWidth = $("speedBinWidthSelect")?.value || "1";
  const data = await api(`/api/speed-histogram${queryString({ bin_width: binWidth })}`);
  const canvas = $("speedCanvas");
  if (speedChart) { speedChart.destroy(); speedChart = null; }
  const total = (data.counts || []).reduce((a, b) => a + b, 0);
  speedChart = new Chart(canvas, {
    type: "bar",
    data: {
      labels: (data.bins || []).slice(0, -1).map((v, i) => `${formatNumber(v, 1)}–${formatNumber(data.bins[i + 1], 1)}`),
      datasets: [{
        label: "频次",
        data: data.counts || [],
        backgroundColor: "#1769aa",
        borderWidth: 0,
        borderRadius: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (ctx) => {
              const i = ctx[0].dataIndex;
              return `航速 ${formatNumber(data.bins[i], 1)}–${formatNumber(data.bins[i + 1], 1)} kn`;
            },
            label: (ctx) => {
              const pct = total > 0 ? (ctx.raw / total * 100) : 0;
              return [`${numberFormat.format(ctx.raw)} 点`, `占比: ${formatNumber(pct, 2)}%`];
            },
          },
        },
      },
      scales: {
        x: { title: { display: true, text: "航速（kn）" }, ticks: { maxTicksLimit: 12 } },
        y: { title: { display: true, text: "点数" } },
      },
    },
  });
}

async function loadShipTypes() {
  const data = await api(`/api/ship-types${queryString({ limit: 30 })}`);
  const canvas = $("shipCanvas");
  if (shipChart) { shipChart.destroy(); shipChart = null; }
  // Aggregate by display name, merge unknown/invalid codes into "未知"
  const nameMap = new Map();
  for (const item of data) {
    const rawName = shipTypeName(item.ship_type);
    const name = rawName.startsWith("类型") ? "未知" : rawName;
    if (nameMap.has(name)) {
      const e = nameMap.get(name);
      e.point_count += item.point_count;
      e.vessel_count += item.vessel_count;
      e.codes.push(item.ship_type);
    } else {
      nameMap.set(name, { point_count: item.point_count, vessel_count: item.vessel_count, codes: [item.ship_type] });
    }
  }
  const agg = [...nameMap.entries()]
    .map(([name, val]) => ({ name, ...val }))
    .sort((a, b) => b.point_count - a.point_count);
  const totalPoints = agg.reduce((s, item) => s + item.point_count, 0);
  const countLabel = state.spatialFilter?.mode === "section" ? "穿越次数" : "点数";
  shipChart = new Chart(canvas, {
    type: "bar",
    data: {
      labels: agg.map((item) => item.name),
      datasets: [{
        label: countLabel,
        data: agg.map((item) => item.point_count),
        backgroundColor: "#2b8a6e",
        borderWidth: 0,
        borderRadius: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (ctx) => agg[ctx[0].dataIndex].name,
            label: (ctx) => {
              const item = agg[ctx.dataIndex];
              const codeStr = item.codes.length <= 5
                ? item.codes.join(", ")
                : item.codes.slice(0, 5).join(", ") + "…";
              const pct = totalPoints > 0 ? (ctx.raw / totalPoints * 100) : 0;
              return [
                `${countLabel}: ${numberFormat.format(ctx.raw)} (${formatNumber(pct, 1)}%)`,
                `船舶: ${numberFormat.format(item.vessel_count)}`,
                `代码: ${codeStr}`,
              ];
            },
          },
        },
      },
      scales: {
        x: { title: { display: true, text: "船型" } },
        y: { title: { display: true, text: countLabel } },
      },
    },
  });
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
          <div class="muted">${numberFormat.format(row.point_count || 0)} ${state.spatialFilter?.mode === "section" ? "次穿越" : "点"}</div>
        </div>
      `;
    })
    .join("");
}

function applyBehaviorFilter() {
  const select = $("behaviorSelect");
  if (!select.value) {
    $("behaviorFilterStatus").textContent = "请先选择行为";
    return;
  }
  state.behaviorFilter = select.value;
  $("behaviorFilterStatus").textContent = `正在显示：${select.options[select.selectedIndex].text}`;
  refreshTrackTiles();
}

function clearBehaviorFilter() {
  state.behaviorFilter = null;
  $("behaviorSelect").value = "";
  $("behaviorFilterStatus").textContent = "显示全部轨迹";
  refreshTrackTiles();
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
  const canvas = $("timeCanvas");
  if (timeChart) { timeChart.destroy(); timeChart = null; }
  const grainLabel = { day: "日期", hour: "时刻", month: "月份" }[grain] || grain;
  const flowLabel = state.spatialFilter?.mode === "section" ? "截面穿越次数" : "轨迹点数";
  timeChart = new Chart(canvas, {
    type: "line",
    data: {
      labels: data.map((item) => item.bucket),
      datasets: [{
        label: flowLabel,
        data: data.map((item) => item.point_count),
        borderColor: "#c97922",
        backgroundColor: "rgba(201,121,34,0.12)",
        borderWidth: 2,
        pointRadius: data.length > 120 ? 0 : 3,
        pointHoverRadius: 5,
        fill: true,
        tension: 0.3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (ctx) => `${grainLabel}: ${ctx[0].label}`,
            label: (ctx) => `${flowLabel}: ${numberFormat.format(ctx.raw)}`,
          },
        },
      },
      scales: {
        x: {
          ticks: { maxTicksLimit: 14, maxRotation: 30 },
          title: { display: true, text: grainLabel },
        },
        y: { title: { display: true, text: flowLabel } },
      },
    },
  });
}

async function loadLengthHistogram() {
  const binWidth = $("lengthBinWidthSelect")?.value || "10";
  const data = await api(`/api/length-histogram${queryString({ bin_width: binWidth })}`);
  const canvas = $("lengthCanvas");
  if (lengthChart) { lengthChart.destroy(); lengthChart = null; }
  const total = (data.counts || []).reduce((a, b) => a + b, 0);
  lengthChart = new Chart(canvas, {
    type: "bar",
    data: {
      labels: (data.bins || []).slice(0, -1).map((v, i) => `${formatNumber(v, 0)}–${formatNumber(data.bins[i + 1], 0)}`),
      datasets: [{
        label: "船舶数",
        data: data.counts || [],
        backgroundColor: "#7c5cbf",
        borderWidth: 0,
        borderRadius: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (ctx) => {
              const i = ctx[0].dataIndex;
              const lo = data.bins[i];
              const hi = data.bins[i + 1];
              return `船长 ${formatNumber(lo, 0)}–${formatNumber(hi, 0)} m`;
            },
            label: (ctx) => {
              const pct = total > 0 ? (ctx.raw / total * 100) : 0;
              return [`${numberFormat.format(ctx.raw)} 艘`, `占比: ${formatNumber(pct, 2)}%`];
            },
          },
        },
      },
      scales: {
        x: { title: { display: true, text: "船长（m）" }, ticks: { maxTicksLimit: 10 } },
        y: { title: { display: true, text: "船舶数" } },
      },
    },
  });
}

function toggleCoordinateFormat() {
  state.coordinateFormat = state.coordinateFormat === "decimal" ? "dms" : "decimal";
  renderMapCoordinates();
}

function renderMapCoordinates() {
  const output = $("mapCoordinates");
  output.title = state.coordinateFormat === "decimal"
    ? "当前为十进制度，点击切换为度分秒"
    : "当前为度分秒，点击切换为十进制度";
  if (!state.cursorLngLat) return;

  const { lng, lat } = state.cursorLngLat;
  output.textContent = state.coordinateFormat === "decimal"
    ? `经度 ${lng.toFixed(6)}　纬度 ${lat.toFixed(6)}`
    : `${formatDegreesMinutesSeconds(lng, true)}　${formatDegreesMinutesSeconds(lat, false)}`;
}

function formatDegreesMinutesSeconds(value, isLongitude) {
  const direction = isLongitude
    ? (value >= 0 ? "东经" : "西经")
    : (value >= 0 ? "北纬" : "南纬");
  let remainingSeconds = Math.round(Math.abs(value) * 360000) / 100;
  const degrees = Math.floor(remainingSeconds / 3600);
  remainingSeconds -= degrees * 3600;
  const minutes = Math.floor(remainingSeconds / 60);
  const seconds = remainingSeconds - minutes * 60;
  return `${direction} ${degrees}°${String(minutes).padStart(2, "0")}′${seconds.toFixed(2).padStart(5, "0")}″`;
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
          tiles: [...window.AISBasemap.tiles],
          tileSize: 256,
          minzoom: 0,
          maxzoom: 19,
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
  const basemap = window.AISBasemap.attach(state.map);
  state.map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-left");
  state.map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
  // Fullscreen and wrapping controls can resize the container without a window resize.
  const resizeObserver = new ResizeObserver(() => state.map?.resize());
  resizeObserver.observe($("map"));
  state.map.on("mousemove", ({ lngLat }) => {
    state.cursorLngLat = { lng: lngLat.lng, lat: lngLat.lat };
    renderMapCoordinates();
  });
  state.map.on("load", () => {
    state.mapLoaded = true;
    initMarkerLayers();
    initDensityLayers();
    renderMarkers();
    $("mapStatus").textContent = state.activeKey ? "正在加载船舶轨迹" : "等待加载数据";
    if (state.activeKey) refreshTrackTiles();
    fitMapToDataOnce();
  });
  state.map.on("error", (event) => {
    if (basemap.handleError(event)) return;
    if (event && event.error) {
      const tracks = event.sourceId === "ais-tracks" || event.error.url?.includes("/api/tiles/tracks/");
      $("mapStatus").textContent = tracks ? "船舶轨迹加载失败，请刷新轨迹图层重试。" : "地图渲染异常，请刷新页面重试。";
      console.warn("地图图层加载失败", event.error);
    }
  });
}

function initMarkerLayers() {
  const map = state.map;
  const empty = { type: "FeatureCollection", features: [] };
  map.addSource("saved-markers", { type: "geojson", data: empty });
  map.addSource("draft-marker", { type: "geojson", data: empty });
  map.addSource("measure", { type: "geojson", data: empty });

  map.addLayer({
    id: "measure-line",
    type: "line",
    source: "measure",
    filter: ["==", ["geometry-type"], "LineString"],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#2563eb", "line-width": 3, "line-dasharray": [2, 1] },
  });
  map.addLayer({
    id: "measure-points",
    type: "circle",
    source: "measure",
    filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-radius": 5, "circle-color": "#2563eb", "circle-stroke-color": "#fff", "circle-stroke-width": 2 },
  });

  map.addLayer({
    id: "marker-region-fill",
    type: "fill",
    source: "saved-markers",
    filter: ["==", ["get", "markerType"], "region"],
    paint: {
      "fill-color": ["get", "color"],
      "fill-opacity": ["case", ["==", ["get", "active"], true], 0.16, 0.08],
    },
  });
  map.addLayer({
    id: "marker-route-line",
    type: "line",
    source: "saved-markers",
    filter: ["==", ["get", "markerType"], "route"],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": ["get", "color"], "line-width": 4, "line-opacity": 0.9 },
  });
  map.addLayer({
    id: "marker-section-line",
    type: "line",
    source: "saved-markers",
    filter: ["==", ["get", "markerType"], "section"],
    layout: { "line-cap": "round" },
    paint: { "line-color": ["get", "color"], "line-width": 5, "line-dasharray": [1.2, 1] },
  });
  map.addLayer({
    id: "marker-region-line",
    type: "line",
    source: "saved-markers",
    filter: ["==", ["get", "markerType"], "region"],
    paint: { "line-color": ["get", "color"], "line-width": 3 },
  });
  map.addLayer({
    id: "marker-point",
    type: "circle",
    source: "saved-markers",
    filter: ["==", ["get", "markerType"], "point"],
    paint: {
      "circle-radius": 7,
      "circle-color": ["get", "color"],
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 2,
    },
  });
  map.addLayer({
    id: "draft-fill",
    type: "fill",
    source: "draft-marker",
    filter: ["==", ["geometry-type"], "Polygon"],
    paint: { "fill-color": "#f59e0b", "fill-opacity": 0.2 },
  });
  map.addLayer({
    id: "draft-line",
    type: "line",
    source: "draft-marker",
    filter: ["==", ["geometry-type"], "LineString"],
    paint: { "line-color": "#f59e0b", "line-width": 3, "line-dasharray": [2, 1] },
  });
  map.addLayer({
    id: "draft-points",
    type: "circle",
    source: "draft-marker",
    filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-radius": 5, "circle-color": "#f59e0b", "circle-stroke-color": "#fff", "circle-stroke-width": 2 },
  });

  ["marker-region-fill", "marker-route-line", "marker-section-line", "marker-region-line", "marker-point"].forEach((layerId) => {
    map.on("click", layerId, showMarkerPopup);
    map.on("mouseenter", layerId, () => { if (!state.drawing && !state.measuring) map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", layerId, () => { if (!state.drawing && !state.measuring) map.getCanvas().style.cursor = ""; });
  });
}

function toggleMapTool(tool) {
  state.openMapTool = state.openMapTool === tool ? null : tool;
  if (state.drawing) stopMarkerDrawing();
  if (state.measuring) stopMeasuring();
  document.querySelectorAll(".marker-groups-section, .marker-create-section, .marker-library, .spatial-section, .measure-section, .behavior-section, .density-section")
    .forEach((element) => { element.hidden = true; });
  if (state.openMapTool === "marker") showMarkerOverview();
  if (state.openMapTool === "filter") document.querySelector(".spatial-section").hidden = false;
  if (state.openMapTool === "measure") {
    document.querySelector(".measure-section").hidden = false;
    startMeasuring();
  }
  if (state.openMapTool === "behavior") {
    document.querySelector(".behavior-section").hidden = false;
  }
  if (state.openMapTool === "density") document.querySelector(".density-section").hidden = false;
  document.querySelector(".map-workbench").classList.toggle("expanded", Boolean(state.openMapTool));
  document.querySelectorAll("[data-map-tool]").forEach((button) => {
    button.classList.toggle("active", button.dataset.mapTool === state.openMapTool);
  });
}

function startMeasuring() {
  if (!state.mapLoaded || state.measuring) return;
  state.measuring = true;
  state.map.getCanvas().classList.add("is-measuring");
  state.map.on("click", onMeasureClick);
}

function stopMeasuring() {
  if (!state.map) return;
  state.measuring = false;
  state.map.off("click", onMeasureClick);
  state.map.getCanvas().classList.remove("is-measuring");
}

function onMeasureClick(event) {
  state.measurePoints.push([event.lngLat.lng, event.lngLat.lat]);
  renderMeasurement();
}

function undoMeasurePoint() {
  state.measurePoints.pop();
  renderMeasurement();
}

function clearMeasurement() {
  state.measurePoints = [];
  renderMeasurement();
}

function renderMeasurement() {
  const source = state.map?.getSource("measure");
  if (!source) return;
  const features = state.measurePoints.map((point) => ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: point } }));
  if (state.measurePoints.length > 1) features.unshift({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: state.measurePoints } });
  source.setData({ type: "FeatureCollection", features });
  const segments = state.measurePoints.slice(1).map((point, index) => haversineDistance(state.measurePoints[index], point));
  const total = segments.reduce((sum, distance) => sum + distance, 0);
  $("measureTotal").textContent = formatDistance(total);
  $("measureSegment").textContent = segments.length ? `最近一段 ${formatDistance(segments.at(-1))} · ${state.measurePoints.length} 个点` : state.measurePoints.length ? "继续点击地图添加下一点" : "尚未添加测量点";
  $("undoMeasureBtn").disabled = !state.measurePoints.length;
  $("clearMeasureBtn").disabled = !state.measurePoints.length;
}

function haversineDistance(first, second) {
  const radians = (degrees) => degrees * Math.PI / 180;
  const lat1 = radians(first[1]);
  const lat2 = radians(second[1]);
  const deltaLat = lat2 - lat1;
  const deltaLon = radians(second[0] - first[0]);
  const value = Math.min(1, Math.sin(deltaLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(deltaLon / 2) ** 2);
  return 6371008.8 * 2 * Math.atan2(Math.sqrt(value), Math.sqrt(1 - value));
}

function formatDistance(meters) {
  if (meters < 1000) return `${meters.toFixed(meters < 100 ? 1 : 0)} m`;
  return `${(meters / 1000).toFixed(2)} km · ${(meters / 1852).toFixed(2)} nmi`;
}

function showMarkerOverview() {
  if (state.drawing) stopMarkerDrawing();
  state.editingMarkerId = null;
  document.querySelector(".marker-groups-section").hidden = false;
  document.querySelector(".marker-library").hidden = false;
  document.querySelector(".marker-create-section").hidden = true;
  updateMarkerGroupCounts();
}

function openMarkerEditor(type) {
  if (!MARKER_RULES[type]) return;
  selectMarkerType(type);
  document.querySelector(".marker-groups-section").hidden = true;
  document.querySelector(".marker-library").hidden = true;
  document.querySelector(".marker-create-section").hidden = false;
  $("markerEditorTitle").textContent = `添加${MARKER_RULES[type].label}标记`;
}

function updateMarkerGroupCounts() {
  document.querySelectorAll("[data-marker-count]").forEach((element) => {
    element.textContent = state.markers.filter((marker) => marker.type === element.dataset.markerCount).length;
  });
}

function selectMarkerType(type) {
  if (!MARKER_RULES[type] || state.drawing) return;
  state.markerType = type;
  document.querySelectorAll("[data-marker-type]").forEach((button) => {
    button.classList.toggle("active", button.dataset.markerType === type);
  });
  $("areaShapeOptions").hidden = type !== "region";
  $("markerDrawHint").textContent = currentMarkerRule().hint;
}

function selectAreaShape(shape) {
  if (!AREA_SHAPE_RULES[shape] || state.drawing) return;
  state.areaShape = shape;
  document.querySelectorAll("[data-area-shape]").forEach((button) => {
    button.classList.toggle("active", button.dataset.areaShape === shape);
  });
  $("markerDrawHint").textContent = currentMarkerRule().hint;
}

const MARKER_RULES = {
  point: { label: "点", minimum: 1, maximum: 1, hint: "单击地图或输入经纬度放置点标记。" },
  section: { label: "截面", minimum: 2, maximum: 2, hint: "在地图点击或输入截面两端坐标；方向由第一个点指向第二个点。" },
  route: { label: "航线", minimum: 2, maximum: Infinity, hint: "在地图点击或输入坐标依次添加航线节点，双击或点击完成。" },
  region: { label: "区域", minimum: 3, maximum: Infinity, hint: "在地图点击或输入坐标沿区域边界添加至少三个点，双击或点击完成。" },
};

const AREA_SHAPE_RULES = {
  polygon: { label: "任意多边形区域", minimum: 3, maximum: Infinity, hint: "在地图点击或输入坐标沿边界依次添加至少三个点，双击或点击完成。" },
  rectangle: { label: "矩形区域", minimum: 4, maximum: 4, hint: "按边界顺序输入或点击矩形的四个顶点。" },
  circle: { label: "圆形区域", minimum: 2, maximum: 2, hint: "先添加圆心，再添加圆周上一点确定半径。" },
};

function currentMarkerRule() {
  return state.markerType === "region" ? AREA_SHAPE_RULES[state.areaShape] : MARKER_RULES[state.markerType];
}

function markerTypeLabel(marker) {
  return marker.type === "region"
    ? (AREA_SHAPE_RULES[marker.areaShape || "polygon"]?.label || "区域")
    : MARKER_RULES[marker.type].label;
}

function beginMarkerDrawing() {
  if (!state.mapLoaded || state.drawing) return;
  state.drawing = true;
  state.draftPoints = [];
  state.draftCursor = null;
  state.map.getCanvas().classList.add("is-marking");
  state.map.doubleClickZoom.disable();
  state.map.on("click", onMarkerClick);
  state.map.on("mousemove", onMarkerMove);
  state.map.on("dblclick", onMarkerDoubleClick);
  $("beginMarkerBtn").disabled = true;
  $("finishMarkerBtn").disabled = false;
  $("cancelMarkerBtn").disabled = false;
  updateCoordinateEntryState();
  const rule = currentMarkerRule();
  $("markerDrawHint").textContent = `正在绘制${rule.label}：${rule.hint}`;
  renderDraftMarker();
}

function onMarkerClick(event) {
  addDraftPoint([event.lngLat.lng, event.lngLat.lat]);
}

function addDraftPoint(point) {
  state.draftPoints.push(point);
  state.draftPoints = dedupePoints(state.draftPoints);
  renderDraftMarker();
  updateCoordinateEntryState();
  const rule = currentMarkerRule();
  $("markerDrawHint").textContent = `已添加 ${state.draftPoints.length} 个节点；${rule.hint}`;
  if (state.draftPoints.length >= rule.maximum) finishMarkerDrawing();
}

function parseCoordinateLines(value) {
  return value.trim().split(/\r?\n/).filter((line) => line.trim()).map((line, index) => {
    const parts = line.includes(",") || line.includes("，")
      ? line.split(/[,，]/).map((part) => part.trim()).filter(Boolean)
      : line.trim().split(/\s+/);
    let values;
    try {
      if (parts.length !== 2) throw new Error();
      values = [parseCoordinateValue(parts[0], "longitude"), parseCoordinateValue(parts[1], "latitude")];
    } catch {
      throw new Error(`第 ${index + 1} 行坐标无效`);
    }
    return values;
  });
}

function parseCoordinateValue(value, axis) {
  const normalized = value.toUpperCase()
    .replace(/东经|东/g, "E").replace(/西经|西/g, "W")
    .replace(/北纬|北/g, "N").replace(/南纬|南/g, "S")
    .replace(/[º˚]/g, "°").replace(/[’']/g, "′").replace(/[”\"]/g, "″");
  const direction = normalized.match(/[NSEW]/)?.[0] || "";
  if ((axis === "longitude" && /[NS]/.test(direction)) || (axis === "latitude" && /[EW]/.test(direction))) throw new Error();
  const numbers = normalized.replace(/[NSEW]/g, "").match(/[+-]?\d+(?:\.\d+)?/g)?.map(Number) || [];
  if (numbers.length < 1 || numbers.length > 3 || numbers.some((number) => !Number.isFinite(number))) throw new Error();
  const [degrees, minutes = 0, seconds = 0] = numbers;
  if (minutes < 0 || minutes >= 60 || seconds < 0 || seconds >= 60) throw new Error();
  const limit = axis === "longitude" ? 180 : 90;
  const absolute = Math.abs(degrees) + minutes / 60 + seconds / 3600;
  if (absolute > limit) throw new Error();
  const sign = direction === "W" || direction === "S" || degrees < 0 ? -1 : 1;
  return sign * absolute;
}

function addCoordinatePoints() {
  let points;
  try {
    points = parseCoordinateLines($("markerCoordinatesInput").value);
    if (!points.length) throw new Error("请至少输入一组坐标");
  } catch (error) {
    $("markerDrawHint").textContent = `${error.message}；格式为“经度, 纬度”。`;
    return;
  }
  points = dedupePoints(points);

  if (state.markerType === "point") {
    savePointMarkers(points);
    return;
  }

  const rule = currentMarkerRule();
  const total = state.draftPoints.length + points.length;
  if (total > rule.maximum) {
    $("markerDrawHint").textContent = `${rule.label}需要 ${rule.maximum} 个点，当前加入后将有 ${total} 个。`;
    return;
  }
  if (!state.drawing) beginMarkerDrawing();
  if (!state.drawing) {
    $("markerDrawHint").textContent = "地图尚未加载完成，请稍后再添加坐标。";
    return;
  }
  state.draftPoints = dedupePoints([...state.draftPoints, ...points]);
  $("markerCoordinatesInput").value = "";
  state.map.fitBounds(points.reduce((bounds, point) => bounds.extend(point), new maplibregl.LngLatBounds()), { padding: 80, maxZoom: 14 });
  renderDraftMarker();
  updateCoordinateEntryState();
  $("markerDrawHint").textContent = `已批量加入 ${points.length} 个点；共 ${state.draftPoints.length} 个节点。`;
  if (state.draftPoints.length >= rule.maximum) finishMarkerDrawing();
}

function savePointMarkers(points) {
  const baseName = $("markerNameInput").value.trim();
  const category = $("markerCategoryInput").value.trim() || "未分类";
  const color = $("markerColorInput").value;
  if (state.editingMarkerId) {
    if (points.length !== 1) {
      $("markerDrawHint").textContent = "重新绘制点标记时只能输入一组坐标。";
      return;
    }
    const marker = state.markers.find((item) => item.id === state.editingMarkerId);
    marker.name = baseName || marker.name;
    marker.category = category;
    marker.color = color;
    marker.points = [[Number(points[0][0].toFixed(8)), Number(points[0][1].toFixed(8))]];
    state.editingMarkerId = null;
    stopMarkerDrawing();
  } else {
  const start = state.markers.filter((item) => item.type === "point").length + 1;
  points.forEach((point, index) => state.markers.push({
    id: Date.now() * 100 + state.markers.length + index,
    type: "point",
    areaShape: null,
    name: points.length === 1 && baseName ? baseName : `${baseName || "点标记"} ${start + index}`,
    category,
    color,
    points: [[Number(point[0].toFixed(8)), Number(point[1].toFixed(8))]],
  }));
  }
  persistMarkers();
  $("markerCoordinatesInput").value = "";
  $("markerNameInput").value = "";
  renderMarkers();
  updateSpatialMarkerOptions();
  state.map.fitBounds(points.reduce((bounds, point) => bounds.extend(point), new maplibregl.LngLatBounds()), { padding: 80, maxZoom: 14 });
  $("markerDrawHint").textContent = `已批量保存 ${points.length} 个点标记。`;
  showMarkerOverview();
}

function undoCoordinatePoint() {
  if (!state.drawing || !state.draftPoints.length) return;
  state.draftPoints.pop();
  state.draftCursor = null;
  renderDraftMarker();
  updateCoordinateEntryState();
  $("markerDrawHint").textContent = state.draftPoints.length
    ? `已保留 ${state.draftPoints.length} 个节点；可继续输入坐标或在地图上选点。`
    : "已撤销全部节点，可继续输入坐标或在地图上选点。";
}

function updateCoordinateEntryState() {
  const count = state.draftPoints.length;
  $("coordinatePointCount").textContent = count ? `已添加 ${count} 个节点` : "尚未添加坐标";
  $("undoCoordinateBtn").disabled = !state.drawing || !count;
}

function onMarkerMove(event) {
  state.draftCursor = [event.lngLat.lng, event.lngLat.lat];
  renderDraftMarker();
}

function onMarkerDoubleClick(event) {
  event.preventDefault();
  finishMarkerDrawing();
}

function finishMarkerDrawing() {
  if (!state.drawing) return;
  state.draftPoints = dedupePoints(state.draftPoints);
  const rule = currentMarkerRule();
  if (state.draftPoints.length < rule.minimum) {
    $("markerDrawHint").textContent = `${rule.label}标记至少需要 ${rule.minimum} 个不同节点。`;
    return;
  }
  const geometryPoints = state.markerType === "region"
    ? regionGeometryPoints(state.draftPoints)
    : state.draftPoints;
  const marker = {
    id: state.editingMarkerId || (Date.now() * 100 + state.markers.length),
    type: state.markerType,
    areaShape: state.markerType === "region" ? state.areaShape : null,
    name: $("markerNameInput").value.trim() || `${rule.label}标记 ${state.markers.filter((item) => item.type === state.markerType).length + 1}`,
    category: $("markerCategoryInput").value.trim() || "未分类",
    color: $("markerColorInput").value,
    points: geometryPoints.map((point) => [Number(point[0].toFixed(8)), Number(point[1].toFixed(8))]),
  };
  const index = state.markers.findIndex((item) => item.id === marker.id);
  if (index >= 0) state.markers[index] = marker;
  else state.markers.push(marker);
  persistMarkers();
  stopMarkerDrawing();
  state.editingMarkerId = null;
  $("markerNameInput").value = "";
  renderMarkers();
  renderMarkerList();
  updateSpatialMarkerOptions();
  $("markerDrawHint").textContent = `${marker.name} 已保存${marker.type === "section" || marker.type === "region" ? "，可以按此标记查看统计" : ""}。`;
  showMarkerOverview();
}

function cancelMarkerDrawing() {
  stopMarkerDrawing();
  state.editingMarkerId = null;
  $("markerDrawHint").textContent = currentMarkerRule().hint;
}

function stopMarkerDrawing() {
  if (!state.map) return;
  state.drawing = false;
  state.draftPoints = [];
  state.draftCursor = null;
  state.map.off("click", onMarkerClick);
  state.map.off("mousemove", onMarkerMove);
  state.map.off("dblclick", onMarkerDoubleClick);
  state.map.doubleClickZoom.enable();
  state.map.getCanvas().classList.remove("is-marking");
  state.map.getCanvas().style.cursor = "";
  $("beginMarkerBtn").disabled = false;
  $("finishMarkerBtn").disabled = true;
  $("cancelMarkerBtn").disabled = true;
  updateCoordinateEntryState();
  renderDraftMarker();
}

function renderDraftMarker() {
  const source = state.map?.getSource("draft-marker");
  if (!source) return;
  const controlPoints = [...state.draftPoints];
  if (state.drawing && state.draftCursor && state.markerType !== "point") controlPoints.push(state.draftCursor);
  const features = state.draftPoints.map((point) => ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: point } }));
  if (controlPoints.length >= 2) {
    const regionPoints = state.markerType === "region" ? regionGeometryPoints(controlPoints) : [];
    const canShowRegion = state.markerType === "region"
      && (state.areaShape === "polygon" ? regionPoints.length >= 3 : regionPoints.length >= 4);
    const geometry = canShowRegion
      ? { type: "Polygon", coordinates: [[...regionPoints, regionPoints[0]]] }
      : { type: "LineString", coordinates: controlPoints };
    features.unshift({ type: "Feature", properties: {}, geometry });
  }
  source.setData({ type: "FeatureCollection", features });
}

function regionGeometryPoints(controlPoints) {
  const points = dedupePoints(controlPoints);
  if (state.areaShape === "polygon" || points.length < 2) return points;
  if (state.areaShape === "rectangle") {
    return points;
  }
  return circlePolygon(points[0], points[1], 48);
}

function circlePolygon(center, edge, vertices) {
  const earthRadius = 6371008.8;
  const lat1 = center[1] * Math.PI / 180;
  const lon1 = center[0] * Math.PI / 180;
  const lat2 = edge[1] * Math.PI / 180;
  const lon2 = edge[0] * Math.PI / 180;
  const dLat = lat2 - lat1;
  const dLon = lon2 - lon1;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  const angularDistance = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  if (!Number.isFinite(angularDistance) || angularDistance * earthRadius < 0.1) return [];
  return Array.from({ length: vertices }, (_, index) => {
    const bearing = index / vertices * Math.PI * 2;
    const lat = Math.asin(
      Math.sin(lat1) * Math.cos(angularDistance)
      + Math.cos(lat1) * Math.sin(angularDistance) * Math.cos(bearing)
    );
    const lon = lon1 + Math.atan2(
      Math.sin(bearing) * Math.sin(angularDistance) * Math.cos(lat1),
      Math.cos(angularDistance) - Math.sin(lat1) * Math.sin(lat)
    );
    return [lon * 180 / Math.PI, lat * 180 / Math.PI];
  });
}

function markerFeature(marker) {
  const properties = {
    markerId: String(marker.id),
    markerType: marker.type,
    name: marker.name,
    category: marker.category,
    color: marker.color,
    areaShape: marker.areaShape || "",
    active: state.spatialFilter?.markerId === marker.id,
  };
  let geometry;
  if (marker.type === "point") geometry = { type: "Point", coordinates: marker.points[0] };
  else if (marker.type === "region") geometry = { type: "Polygon", coordinates: [[...marker.points, marker.points[0]]] };
  else geometry = { type: "LineString", coordinates: marker.points };
  return { type: "Feature", properties, geometry };
}

function renderMarkers() {
  const source = state.map?.getSource("saved-markers");
  if (source) source.setData({ type: "FeatureCollection", features: state.markers.map(markerFeature) });
  renderMarkerList();
}

function renderMarkerList() {
  const container = $("markerList");
  updateMarkerGroupCounts();
  if (!state.markers.length) {
    container.innerHTML = '<div class="empty-state">尚未保存标记</div>';
    return;
  }
  container.innerHTML = state.markers.map((marker) => `
    <article class="marker-item ${state.spatialFilter?.markerId === marker.id ? "active" : ""}">
      <button class="marker-main" onclick="zoomToMarker(${marker.id})">
        <i style="--marker-color:${escapeHtml(marker.color)}"></i>
        <span><b>${escapeHtml(marker.name)}</b><small>${markerTypeLabel(marker)} · ${escapeHtml(marker.category)} · ${marker.type === "region" && (marker.areaShape || "polygon") !== "polygon" ? "自动生成边界" : `${marker.points.length} 节点`}</small></span>
      </button>
      <div class="marker-actions">
        <button class="edit" onclick="editMarker(${marker.id})" title="重新绘制" aria-label="重新绘制"><svg class="ui-icon" aria-hidden="true"><use href="#i-edit" /></svg></button>
        <button class="delete" onclick="deleteMarker(${marker.id})" title="删除" aria-label="删除"><svg class="ui-icon" aria-hidden="true"><use href="#i-trash" /></svg></button>
      </div>
    </article>
  `).join("");
}

function loadMarkers() {
  try {
    const saved = JSON.parse(localStorage.getItem(`ais-platform-markers:user_${AIS.user.id}`) || "[]");
    state.markers = Array.isArray(saved) ? saved.filter(validStoredMarker) : [];
  } catch {
    state.markers = [];
  }
  renderMarkerList();
  updateSpatialMarkerOptions();
}

function validStoredMarker(marker) {
  return marker && MARKER_RULES[marker.type] && Array.isArray(marker.points) && marker.points.length >= MARKER_RULES[marker.type].minimum;
}

function persistMarkers() {
  localStorage.setItem(`ais-platform-markers:user_${AIS.user.id}`, JSON.stringify(state.markers));
}

function editMarker(id) {
  const marker = state.markers.find((item) => item.id === id);
  if (!marker || state.drawing) return;
  openMarkerEditor(marker.type);
  if (marker.type === "region") selectAreaShape(marker.areaShape || "polygon");
  state.editingMarkerId = marker.id;
  $("markerNameInput").value = marker.name;
  $("markerCategoryInput").value = marker.category;
  $("markerColorInput").value = marker.color;
  beginMarkerDrawing();
  $("markerDrawHint").textContent = `正在重新绘制 ${marker.name}，保存后替换原标记。`;
}

function deleteMarker(id) {
  const marker = state.markers.find((item) => item.id === id);
  if (!marker || !window.confirm(`删除标记“${marker.name}”？`)) return;
  if (state.spatialFilter?.markerId === id) clearSpatialFilter();
  state.markers = state.markers.filter((item) => item.id !== id);
  persistMarkers();
  renderMarkers();
  updateSpatialMarkerOptions();
}

function zoomToMarker(id) {
  const marker = state.markers.find((item) => item.id === id);
  if (!marker || !state.map) return;
  if (marker.type === "point") {
    state.map.flyTo({ center: marker.points[0], zoom: 14 });
    return;
  }
  const bounds = marker.points.reduce((box, point) => box.extend(point), new maplibregl.LngLatBounds(marker.points[0], marker.points[0]));
  state.map.fitBounds(bounds, { padding: 80, maxZoom: 14 });
}

function showMarkerPopup(event) {
  if (state.drawing) return;
  const feature = event.features?.[0];
  if (!feature) return;
  new maplibregl.Popup({ closeButton: false, offset: 12 })
    .setLngLat(event.lngLat)
    .setHTML(`<strong>${escapeHtml(feature.properties.name)}</strong><br><span>${feature.properties.markerType === "region" ? AREA_SHAPE_RULES[feature.properties.areaShape || "polygon"].label : MARKER_RULES[feature.properties.markerType].label} · ${escapeHtml(feature.properties.category)}</span>`)
    .addTo(state.map);
}

function dedupePoints(points) {
  return points.filter((point, index) => index === 0 || Math.abs(point[0] - points[index - 1][0]) > 1e-9 || Math.abs(point[1] - points[index - 1][1]) > 1e-9);
}

function updateSpatialMarkerOptions() {
  const mode = $("spatialModeSelect")?.value || "";
  const select = $("spatialMarkerSelect");
  if (!select) return;
  const markers = state.markers.filter((marker) => marker.type === mode);
  const current = select.value;
  select.innerHTML = markers.length
    ? markers.map((marker) => `<option value="${marker.id}">${escapeHtml(marker.name)}</option>`).join("")
    : '<option value="">暂无可用标记</option>';
  if (markers.some((marker) => String(marker.id) === current)) select.value = current;
  $("applySpatialBtn").disabled = !mode || !markers.length;
}

async function applySpatialFilter() {
  const mode = $("spatialModeSelect").value;
  const markerId = Number($("spatialMarkerSelect").value);
  const marker = state.markers.find((item) => item.id === markerId && item.type === mode);
  if (!marker) return;
  state.spatialFilter = { mode, markerId };
  state.filters = collectFilters();
  $("spatialFilterStatus").textContent = `正在统计：${marker.name}`;
  $("spatialTrafficStats").textContent = "正在统计该标记范围…";
  renderMarkers();
  await loadAll();
  refreshTrackTiles();
  zoomToMarker(marker.id);
}

async function clearSpatialFilter() {
  state.spatialFilter = null;
  state.filters = collectFilters();
  $("spatialFilterStatus").textContent = "正在查看全部数据";
  $("spatialTrafficStats").textContent = "选择截面或区域标记后显示交通流统计";
  renderMarkers();
  await loadAll();
  refreshTrackTiles();
}

function renderSpatialTrafficStats(data) {
  const container = $("spatialTrafficStats");
  if (!container || !state.spatialFilter) return;
  const section = state.spatialFilter.mode === "section";
  container.innerHTML = `
    <span><b>${numberFormat.format(data.vessel_count || 0)}</b> 艘船舶</span>
    <span><b>${numberFormat.format(data.trajectory_count || 0)}</b> 条轨迹</span>
    <span><b>${numberFormat.format(data.point_count || 0)}</b> ${section ? "次穿越" : "个 AIS 点"}</span>
    <span><b>${formatNumber(data.speed_avg, 2)}</b> kn 平均航速</span>
    ${section ? `<span><b>${numberFormat.format(data.forward_crossings || 0)}</b> 次正向</span><span><b>${numberFormat.format(data.reverse_crossings || 0)}</b> 次反向</span>` : ""}
  `;
  const marker = state.markers.find((item) => item.id === state.spatialFilter.markerId);
  $("spatialFilterStatus").textContent = marker ? `正在查看：${marker.name}` : "正在按标记查看";
}

function refreshTrackTiles() {
  const map = state.map;
  if (!map || !state.mapLoaded) return;

  const sourceId = "ais-tracks";
  const layerId = "ais-tracks-raster";
  if (map.getLayer(layerId)) map.removeLayer(layerId);
  if (map.getSource(sourceId)) map.removeSource(sourceId);
  if (!state.manifest) {
    clearDensityData();
    $("mapStatus").textContent = "等待加载数据";
    return;
  }
  const tileUrl = `${location.origin}/api/tiles/tracks/{z}/{x}/{y}.png${queryString({
    data_version: state.activeKey,
    render_version: "orange-v7",
    behavior: state.behaviorFilter,
  })}`;
  map.addSource(sourceId, {
    type: "raster",
    tiles: [tileUrl],
    tileSize: 256,
    minzoom: 8,
    maxzoom: 17,
  });
  map.addLayer({
    id: layerId,
    type: "raster",
    source: sourceId,
    minzoom: 8,
    paint: {
      "raster-opacity": 0.95,
      "raster-hue-rotate": 180,
      "raster-saturation": 0.5,
      "raster-fade-duration": 0,
    },
  });
  ["marker-region-fill", "marker-route-line", "marker-section-line", "marker-region-line", "marker-point", "draft-fill", "draft-line", "draft-points"].forEach((id) => {
    if (map.getLayer(id)) map.moveLayer(id);
  });
  updateDensityStyle();
  refreshDensityLayer();
  $("mapStatus").textContent = "正在加载轨迹地图";
  map.once("idle", () => {
    $("mapStatus").textContent = state.behaviorFilter
      ? `已显示${$("behaviorSelect").options[$("behaviorSelect").selectedIndex].text}轨迹`
      : state.spatialFilter ? "已显示所选标记对应的轨迹" : "轨迹地图已更新";
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
  const shipTypeVal = $("shipTypeInput")?.value || "";
  let shipTypeMin = null, shipTypeMax = null;
  if (shipTypeVal) {
    const parts = shipTypeVal.split(":");
    shipTypeMin = parseInt(parts[0], 10);
    shipTypeMax = parseInt(parts[1] ?? parts[0], 10);
  }
  const spatialMarker = state.spatialFilter
    ? state.markers.find((marker) => marker.id === state.spatialFilter.markerId)
    : null;
  const fields = {
    start_ts: datetimeToTs($("startInput").value),
    end_ts: datetimeToTs($("endInput").value),
    lon_min: valueOrNull("lonMinInput"),
    lon_max: valueOrNull("lonMaxInput"),
    lat_min: valueOrNull("latMinInput"),
    lat_max: valueOrNull("latMaxInput"),
    ship_type_min: shipTypeMin,
    ship_type_max: shipTypeMax,
    mmsi: valueOrNull("mmsiFilterInput"),
    speed_min: valueOrNull("speedMinInput"),
    speed_max: valueOrNull("speedMaxInput"),
    spatial_mode: spatialMarker ? state.spatialFilter.mode : null,
    spatial_geometry: spatialMarker ? JSON.stringify(spatialMarker.points) : null,
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

function resetFilters() {
  state.spatialFilter = null;
  state.behaviorFilter = null;
  ["startInput", "endInput", "lonMinInput", "lonMaxInput", "latMinInput", "latMaxInput", "shipTypeInput", "mmsiFilterInput", "speedMinInput", "speedMaxInput"].forEach((id) => {
    $(id).value = "";
  });
  $("spatialFilterStatus").textContent = "正在查看全部数据";
  $("spatialTrafficStats").textContent = "选择截面或区域标记后显示交通流统计";
  $("behaviorSelect").value = "";
  $("behaviorFilterStatus").textContent = "显示全部轨迹";
  state.filters = {};
  renderMarkers();
}

function clearFilters() {
  resetFilters();
  loadAll();
  refreshTrackTiles();
}

function queryString(extra = {}) {
  const params = new URLSearchParams();
  Object.entries({ dataset_id: state.activeDatasetId, ...state.filters, ...extra }).forEach(([key, value]) => {
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

function tsToDatetimeLocal(ts) {
  if (!ts) return "";
  const d = new Date(Number(ts) * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}







function exportExcel() {
  window.location.href = `/api/export/excel${queryString({ direction_bin_width: $("directionBinWidthSelect").value })}`;
}
