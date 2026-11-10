const trackAnnotations = { layer: null, selection: null, request: null, timer: null, frame: null, points: [] };
const TRACK_ENDPOINTS = {
  start: ["起点", "start"], end: ["终点", "end"], both: ["起止点", "both"],
};
const TRACK_ACTIVITIES = {
  accelerate: ["加速", "accelerate"], decelerate: ["减速", "decelerate"], stop: ["停", "stop"],
};
const TRACK_SVG = "http://www.w3.org/2000/svg";

function initTrackAnnotations(map) {
  const layer = document.createElement("div");
  layer.className = "track-annotation-layer";
  layer.setAttribute("aria-hidden", "true");
  map.getCanvasContainer().append(layer);
  trackAnnotations.layer = layer;
  map.on("move", queueTrackAnnotationDraw);
  map.on("resize", queueTrackAnnotationDraw);
  map.on("moveend", () => {
    if (trackAnnotations.selection?.viewport) scheduleTrackAnnotations();
    queueTrackAnnotationDraw();
  });
}

function clearTrackAnnotations() {
  clearTimeout(trackAnnotations.timer);
  if (trackAnnotations.frame !== null) cancelAnimationFrame(trackAnnotations.frame);
  trackAnnotations.request?.abort();
  trackAnnotations.frame = trackAnnotations.request = trackAnnotations.selection = null;
  trackAnnotations.points = [];
  trackAnnotations.layer?.replaceChildren();
  $("trackAnnotationHint").hidden = true;
}

function showTrackAnnotations(params, viewport) {
  trackAnnotations.selection = { params, viewport };
  loadTrackAnnotations();
}

function scheduleTrackAnnotations() {
  clearTimeout(trackAnnotations.timer);
  trackAnnotations.request?.abort();
  trackAnnotations.timer = setTimeout(loadTrackAnnotations, 140);
}

async function loadTrackAnnotations() {
  const selection = trackAnnotations.selection;
  if (!selection) return;
  trackAnnotations.request?.abort();
  const controller = new AbortController();
  trackAnnotations.request = controller;
  const hint = $("trackAnnotationHint");
  try {
    const windows = selection.viewport ? trackViewportWindows(state.map.getBounds()) : [null];
    const results = await Promise.all(windows.map((bounds) => {
      const params = new URLSearchParams(selection.params);
      if (bounds) Object.entries(bounds).forEach(([key, value]) => params.set(key, value));
      return trackInspectionJson("/api/track-inspection/annotations", "?" + params, controller.signal);
    }));
    if (trackAnnotations.request !== controller || controller.signal.aborted) return;
    const points = new Map();
    for (const result of results) {
      for (const point of result.points) points.set(point.index, point);
    }
    trackAnnotations.points = [...points.values()];
    hint.hidden = false;
    hint.textContent = results.some((result) => result.limited)
      ? "轨迹点较密，放大地图可加载其余点位及时间。"
      : "时间为北京时间。标签重叠时会自动避让，放大可查看细节。";
    queueTrackAnnotationDraw();
  } catch (error) {
    if (trackAnnotations.request !== controller || controller.signal.aborted) return;
    hint.hidden = false;
    hint.textContent = error.message;
  } finally {
    if (trackAnnotations.request === controller) trackAnnotations.request = null;
  }
}

function queueTrackAnnotationDraw() {
  if (!trackAnnotations.selection || trackAnnotations.frame !== null) return;
  trackAnnotations.frame = requestAnimationFrame(() => {
    trackAnnotations.frame = null;
    drawTrackAnnotations();
  });
}

function trackAnnotationPath(svg, name, segments) {
  if (!segments.length) return;
  const path = document.createElementNS(TRACK_SVG, "path");
  path.setAttribute("class", name);
  path.setAttribute("d", segments.join(" "));
  svg.append(path);
}

function trackArrow(from, to, width, height) {
  const dx = to.x - from.x, dy = to.y - from.y;
  const length = Math.hypot(dx, dy);
  if (length < 22) return "";
  const x = (from.x + to.x) / 2, y = (from.y + to.y) / 2;
  if (x < 0 || x > width || y < 0 || y > height) return "";
  const ux = dx / length, uy = dy / length;
  return `M ${x + ux * 7} ${y + uy * 7} L ${x - ux * 6 - uy * 5} ${y - uy * 6 + ux * 5} L ${x - ux * 6 + uy * 5} ${y - uy * 6 - ux * 5} Z`;
}

function trackLabelBox(pixel, width, height, size, placed) {
  const { x, y } = pixel;
  const labelHeight = 20;
  const offsets = [
    [13, -labelHeight - 8], [13, 8], [-size - 13, -labelHeight - 8], [-size - 13, 8],
    [-size / 2, -labelHeight - 14], [-size / 2, 14],
    [13, -labelHeight - 38], [13, 38], [-size - 13, -labelHeight - 38], [-size - 13, 38],
    [13, -labelHeight - 68], [13, 68], [-size - 13, -labelHeight - 68], [-size - 13, 68],
  ];
  for (const [dx, dy] of offsets) {
    const left = x + dx, top = y + dy, right = left + size, bottom = top + labelHeight;
    if (left < 3 || top < 3 || right > width - 3 || bottom > height - 3) continue;
    if (placed.some((box) => left < box.right + 3 && right + 3 > box.left && top < box.bottom + 3 && bottom + 3 > box.top)) continue;
    return { left, top, right, bottom };
  }
  return null;
}

function trackAnnotationLabel(entry, width, height, placed, leaders) {
  const { point, pixel } = entry;
  const badges = [TRACK_ENDPOINTS[point.endpoint], TRACK_ACTIVITIES[point.activity]].filter(Boolean);
  const time = trackTime(point.timestamp);
  const badgeWidth = badges.reduce((sum, [text]) => sum + text.length * 12 + 10, 0);
  let labelTime = true;
  let box = trackLabelBox(pixel, width, height, 122 + badgeWidth, placed);
  if (!box && badges.length) {
    labelTime = false;
    box = trackLabelBox(pixel, width, height, badgeWidth, placed);
  }
  if (!box) return null;
  placed.push(box);
  const label = document.createElement("div");
  label.className = "track-annotation-label";
  label.style.left = box.left + "px";
  label.style.top = box.top + "px";
  for (const [text, tone] of badges) {
    const badge = document.createElement("b");
    badge.className = `track-annotation-badge is-${tone}`;
    badge.textContent = text;
    label.append(badge);
  }
  if (labelTime) {
    const stamp = document.createElement("span");
    stamp.className = "track-annotation-time";
    stamp.textContent = time;
    label.append(stamp);
  }
  const endX = Math.max(box.left, Math.min(box.right, pixel.x));
  const endY = pixel.y < box.top ? box.top : box.bottom;
  leaders.push(`M ${pixel.x} ${pixel.y} L ${endX} ${endY}`);
  return label;
}

function drawTrackAnnotations() {
  const { layer, points } = trackAnnotations;
  const map = state.map;
  const width = layer.clientWidth, height = layer.clientHeight;
  if (!width || !height) return;
  const center = map.getCenter().lng;
  const project = (lon, lat) => map.project([lon + 360 * Math.round((center - lon) / 360), lat]);
  const svg = document.createElementNS(TRACK_SVG, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const arrows = [], leaders = [], labels = [], visible = [];
  for (const point of points) {
    const pixel = project(point.lon, point.lat);
    if (point.previous) {
      const arrow = trackArrow(project(...point.previous), pixel, width, height);
      if (arrow) arrows.push(arrow);
    }
    if (pixel.x >= 0 && pixel.x <= width && pixel.y >= 0 && pixel.y <= height) visible.push({ point, pixel });
  }
  const popup = trackInspector.selectedPopup?.getElement();
  const placed = [];
  if (popup) {
    const view = layer.getBoundingClientRect();
    const box = popup.getBoundingClientRect();
    placed.push({ left: box.left - view.left, right: box.right - view.left,
      top: box.top - view.top, bottom: box.bottom - view.top });
  }
  const priority = ({ point }) => point.endpoint ? 2 : point.activity ? 1 : 0;
  visible.sort((a, b) => priority(b) - priority(a));
  for (const entry of visible) {
    const label = trackAnnotationLabel(entry, width, height, placed, leaders);
    if (label) labels.push(label);
  }
  trackAnnotationPath(svg, "track-annotation-leader", leaders);
  trackAnnotationPath(svg, "track-annotation-arrow", arrows);
  for (let index = visible.length - 1; index >= 0; index--) {
    const { point, pixel } = visible[index];
    const dot = document.createElementNS(TRACK_SVG, "circle");
    dot.setAttribute("class", point.endpoint ? `track-annotation-dot is-${point.endpoint}` : "track-annotation-dot");
    dot.setAttribute("cx", pixel.x);
    dot.setAttribute("cy", pixel.y);
    dot.setAttribute("r", point.endpoint ? 6.5 : 3.4);
    svg.append(dot);
  }
  layer.replaceChildren(svg, ...labels);
}
