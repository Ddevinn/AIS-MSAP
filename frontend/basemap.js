"use strict";

window.AISBasemap = {
  tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],

  attach(map) {
    const failures = new Map();
    const delays = [1500, 4500];
    let attempts = 0, timer = null, disposed = false, waitingForContent = false;
    let coastStatus = null, coastActive = false, switcher = null;
    const text = document.getElementById("basemapStatusText");
    const panel = document.getElementById("basemapStatus");
    const button = document.getElementById("retryBasemapBtn");
    function show(message) { if (text.textContent !== message) text.textContent = message; }

    function key(event) {
      const id = event.coord || event.tile?.tileID;
      return id?.key ?? event.error?.url ?? "source";
    }

    function visible(failure) {
      const id = failure.coord;
      if (!id?.canonical || !map.getBounds) return true;
      const { z, x, y } = id.canonical;
      // MapLibre's 512 px world scale uses z+1 for this 256 px raster source.
      // A failed tile from a previous zoom no longer describes the viewport.
      if (map.getZoom && z !== Math.min(19, Math.max(0, Math.round(map.getZoom() + 1)))) return false;
      const size = 2 ** z;
      const west = x / size * 360 - 180 + (id.wrap || 0) * 360;
      const east = west + 360 / size;
      const latitude = value => Math.atan(Math.sinh(Math.PI * (1 - 2 * value / size))) * 180 / Math.PI;
      const bounds = map.getBounds();
      return east >= bounds.getWest() && west <= bounds.getEast()
        && latitude(y) >= bounds.getSouth() && latitude(y + 1) <= bounds.getNorth();
    }

    function currentFailures() { return [...failures.values()].filter(visible); }

    function render() {
      if (disposed || coastActive) return;
      const missing = currentFailures();
      panel.classList.toggle("has-error", missing.length > 0);
      if (missing.length) {
        show(timer !== null
          ? `底图有 ${missing.length} 个区域未加载，正在重试…`
          : `底图有 ${missing.length} 个区域未加载，请检查网络或重新加载底图。`);
      } else {
        const loaded = !waitingForContent && map.getSource("osm") && map.isSourceLoaded("osm");
        show(loaded ? "底图已加载" : "底图加载中…");
        // MapLibre also considers errored tiles finished; the failure collection
        // must be checked before reporting a healthy viewport.
      }
    }

    function cancelRetry() {
      if (timer !== null) clearTimeout(timer);
      timer = null;
    }

    function reload(manual = true) {
      const source = map.getSource("osm");
      if (!source || disposed || coastActive) return;
      cancelRetry();
      if (manual) attempts = 0;
      failures.clear();
      waitingForContent = true;
      panel.classList.remove("has-error");
      show(manual ? "正在重新加载底图…" : `正在重试底图（${attempts}/${delays.length}）…`);
      // The public API reloads errored tiles as well. Keep URLs/cache headers
      // unchanged: successful browser-cached tiles need no new download.
      source.setTiles([...window.AISBasemap.tiles]);
    }

    function scheduleRetry() {
      if (coastActive) return;
      const missing = currentFailures();
      if (missing.some(failure => failure.blocked)) return;
      const retryable = missing.some(failure => failure.retryable);
      if (!retryable || timer !== null || attempts >= delays.length || disposed) return;
      timer = setTimeout(() => {
        timer = null;
        if (!currentFailures().some(failure => failure.retryable) || disposed) { render(); return; }
        attempts++;
        reload(false);
      }, delays[attempts]);
    }

    function handleError(event) {
      const url = event.error?.url || "";
      if (event.sourceId === "coastline" || url.includes("/api/tiles/coastline/")) {
        if (coastActive && event.error?.name !== "AbortError") {
          panel.classList.add("has-error");
          show("岸线瓦片加载失败。可切回 OSM 底图或刷新页面。");
        }
        return true;
      }
      if (event.sourceId !== "osm" && !url.startsWith("https://tile.openstreetmap.org/")) return false;
      if (event.error?.name === "AbortError") return true;
      const status = Number(event.error?.status || 0);
      failures.set(key(event), { coord: event.coord || event.tile?.tileID,
        retryable: !status || status >= 500, blocked: [401, 403, 429].includes(status) });
      // Policy/rate-limit rejections need user action; do not hammer the provider.
      if (status === 401 || status === 403 || status === 429) cancelRetry();
      else scheduleRetry();
      render();
      console.warn("底图瓦片加载失败", { status, url, message: event.error?.message });
      return true;
    }

    function onData(event) {
      if (event.sourceId !== "osm") return;
      if (event.sourceDataType === "content") waitingForContent = false;
      if (event.tile?.state === "loaded") failures.delete(key(event));
      if (!currentFailures().length) cancelRetry();
      render();
    }

    function setMode(useCoastline) {
      if (useCoastline && !coastStatus?.available) return;
      coastActive = useCoastline;
      if (useCoastline && !map.getSource("coastline")) {
        map.addSource("coastline", {
          type: "vector",
          tiles: [location.origin + "/api/tiles/coastline/{z}/{x}/{y}.pbf?version=" + encodeURIComponent(coastStatus.version)],
          minzoom: 7,
          maxzoom: 17,
          attribution: '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">© OpenStreetMap contributors</a>',
        });
        map.addLayer({
          id: "coastline-background",
          type: "background",
          paint: { "background-color": "#e9f4f7" },
        }, "osm");
        map.addLayer({
          id: "coastline",
          type: "line",
          source: "coastline",
          "source-layer": "coastline",
          layout: { "line-join": "round", "line-cap": "round" },
          paint: {
            "line-color": "#21727d",
            "line-width": ["interpolate", ["linear"], ["zoom"], 7, 1, 16, 1.7],
          },
        }, "osm");
      }
      map.setLayoutProperty("osm", "visibility", useCoastline ? "none" : "visible");
      for (const id of ["coastline-background", "coastline"]) {
        if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", useCoastline ? "visible" : "none");
      }
      switcher.querySelector('[data-mode="osm"]').setAttribute("aria-pressed", String(!useCoastline));
      switcher.querySelector('[data-mode="coastline"]').setAttribute("aria-pressed", String(useCoastline));
      button.hidden = useCoastline;
      if (useCoastline) {
        cancelRetry();
        panel.classList.remove("has-error");
        show("当前底图：仅岸线");
      } else {
        if (currentFailures().length) reload(false);
        render();
      }
    }

    function mountSwitcher() {
      const control = {
        onAdd() {
          switcher = document.createElement("div");
          switcher.className = "maplibregl-ctrl basemap-switcher";
          switcher.setAttribute("role", "group");
          switcher.setAttribute("aria-label", "底图切换");
          for (const [mode, label] of [["osm", "OSM 底图"], ["coastline", "仅岸线"]]) {
            const option = document.createElement("button");
            option.type = "button";
            option.dataset.mode = mode;
            option.textContent = label;
            option.setAttribute("aria-pressed", String(mode === "osm"));
            option.disabled = mode === "coastline";
            option.addEventListener("click", () => setMode(mode === "coastline"));
            switcher.append(option);
          }
          return switcher;
        },
        onRemove() { switcher?.remove(); },
      };
      map.addControl(control, "bottom-right");
      const coastOption = switcher.querySelector('[data-mode="coastline"]');
      coastOption.title = "正在检查本地岸线数据";
      window.AIS.json("/api/coastline/status").then(status => {
        coastStatus = status;
        coastOption.disabled = !status.available;
        coastOption.title = status.available ? "显示本地岸线" : "本机尚未配置岸线数据";
      }).catch(() => { coastOption.title = "岸线数据状态读取失败，请刷新页面"; });
    }

    function onMove() { scheduleRetry(); render(); }
    function onClick() { reload(true); }
    function dispose() {
      disposed = true;
      cancelRetry();
      button.removeEventListener("click", onClick);
      map.off("sourcedata", onData);
      map.off("idle", render);
      map.off("moveend", onMove);
      map.off("remove", dispose);
    }

    button.addEventListener("click", onClick);
    map.on("sourcedata", onData);
    map.on("idle", render);
    map.on("moveend", onMove);
    map.on("remove", dispose);
    map.once("load", mountSwitcher);
    render();
    return { handleError, reload, dispose };
  },
};
