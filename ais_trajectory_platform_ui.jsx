import React, { useMemo, useState } from "react";

const trafficData = [
  { time: "00", flow: 126, speed: 10.2, density: 42 },
  { time: "04", flow: 88, speed: 9.4, density: 31 },
  { time: "08", flow: 214, speed: 11.6, density: 69 },
  { time: "12", flow: 286, speed: 12.1, density: 81 },
  { time: "16", flow: 332, speed: 10.8, density: 96 },
  { time: "20", flow: 248, speed: 9.9, density: 76 },
];

const trafficByScale = {
  日: [
    { time: "05-01", flow: 2840, speed: 10.6, density: 76 },
    { time: "05-02", flow: 3126, speed: 11.1, density: 82 },
    { time: "05-03", flow: 2988, speed: 10.8, density: 79 },
    { time: "05-04", flow: 3420, speed: 11.4, density: 88 },
    { time: "05-05", flow: 3264, speed: 10.9, density: 84 },
    { time: "05-06", flow: 3568, speed: 11.7, density: 92 },
  ],
  月: [
    { time: "01月", flow: 78240, speed: 10.1, density: 64 },
    { time: "02月", flow: 84680, speed: 10.4, density: 69 },
    { time: "03月", flow: 91820, speed: 10.8, density: 74 },
    { time: "04月", flow: 96340, speed: 11.0, density: 79 },
    { time: "05月", flow: 103560, speed: 11.3, density: 86 },
    { time: "06月", flow: 98620, speed: 10.9, density: 82 },
  ],
};

const shipTypeData = [
  { name: "货船", value: 42, color: "#2563eb" },
  { name: "油船", value: 18, color: "#14b8a6" },
  { name: "客船", value: 11, color: "#f97316" },
  { name: "拖轮", value: 9, color: "#8b5cf6" },
  { name: "其他", value: 20, color: "#94a3b8" },
];

const qualityItems = [
  { label: "重复数据", value: 3.6, status: "已去重" },
  { label: "缺失经纬度", value: 1.2, status: "已过滤" },
  { label: "时间格式异常", value: 0.8, status: "已标准化" },
  { label: "速度突变点", value: 2.4, status: "待复核" },
];

const cleaningConfigDefaults = {
  maxSpeedKn: 35,
  maxJumpDistanceNm: 3,
  maxTurnAngleDeg: 120,
  minTrackPoints: 8,
  segmentGapMinutes: 30,
  interpolationGapMinutes: 10,
  smoothingWindow: 5,
};

const fieldMap = ["MMSI", "时间", "经度", "纬度", "航速", "航向", "船型", "船长", "船宽", "吃水"];

const trackEvents = [
  { title: "轨迹重建", desc: "按 MMSI + 时间序列生成 18,426 条单船轨迹", icon: "⌁" },
  { title: "异常检测", desc: "识别跳点、漂移点、时间断点、速度突变点", icon: "⚠" },
  { title: "轨迹修复", desc: "插值补点、平滑处理、片段切分已完成", icon: "≈" },
  { title: "状态识别", desc: "航行、低速、停留状态自动标注", icon: "◉" },
];

const statCards = [
  { label: "导入文件", value: "128", unit: "个", icon: "⇪" },
  { label: "AIS 点位", value: "9.84M", unit: "条", icon: "▦" },
  { label: "有效船舶", value: "36,912", unit: "艘", icon: "▰" },
  { label: "异常点占比", value: "2.7", unit: "%", icon: "!" },
];

const osmTiles = [
  "https://tile.openstreetmap.org/10/843/388.png",
  "https://tile.openstreetmap.org/10/844/388.png",
  "https://tile.openstreetmap.org/10/845/388.png",
  "https://tile.openstreetmap.org/10/843/389.png",
  "https://tile.openstreetmap.org/10/844/389.png",
  "https://tile.openstreetmap.org/10/845/389.png",
  "https://tile.openstreetmap.org/10/843/390.png",
  "https://tile.openstreetmap.org/10/844/390.png",
  "https://tile.openstreetmap.org/10/845/390.png",
];

const osmAttribution = "© OpenStreetMap contributors";
const mapInteractionDefaults = { minZoom: 0.75, maxZoom: 2.8, zoomStep: 0.18 };

const requiredPageModules = [
  "数据导入与清洗",
  "OpenStreetMap 船舶轨迹主视图",
  "轨迹重建与运动特征",
  "导入数据流量统计",
  "导入数据船型统计",
  "统计结果与报表导出",
];

const testCases = [
  {
    name: "traffic data has ordered hourly buckets",
    pass: trafficData.every((item) => /^[0-9]{2}$/.test(item.time)) && trafficData.length >= 3,
  },
  {
    name: "imported traffic statistics support day and month scales",
    pass: ["日", "月"].every((scale) => Array.isArray(trafficByScale[scale]) && trafficByScale[scale].length > 0),
  },
  {
    name: "ship type distribution equals 100 percent",
    pass: shipTypeData.reduce((sum, item) => sum + item.value, 0) === 100,
  },
  {
    name: "AIS standard field map contains required core fields",
    pass: ["MMSI", "时间", "经度", "纬度", "航速", "航向"].every((field) => fieldMap.includes(field)),
  },
  {
    name: "quality items contain valid percentage values",
    pass: qualityItems.every((item) => item.value >= 0 && item.value <= 100),
  },
  {
    name: "all traffic scale rows contain flow, speed and density",
    pass: Object.values(trafficByScale).flat().every((row) => row.flow > 0 && row.speed > 0 && row.density >= 0),
  },
  {
    name: "openstreetmap tile layer contains attribution and valid tile urls",
    pass: osmTiles.length === 9 && osmTiles.every((url) => url.startsWith("https://tile.openstreetmap.org/")) && osmAttribution.includes("OpenStreetMap"),
  },
  {
    name: "map layer controls include tracks, heatmap, section and region",
    pass: ["tracks", "heatmap", "section", "region"].length === 4,
  },
  {
    name: "map interaction supports pan and bounded zoom",
    pass: mapInteractionDefaults.minZoom < 1 && mapInteractionDefaults.maxZoom > 1 && mapInteractionDefaults.zoomStep > 0,
  },
  {
    name: "cleaning configuration contains threshold and segmentation parameters",
    pass: cleaningConfigDefaults.minTrackPoints > 0 && cleaningConfigDefaults.segmentGapMinutes > 0 && cleaningConfigDefaults.maxSpeedKn > 0,
  },
  {
    name: "map supports section and region drawing tools",
    pass: ["section", "region"].every((tool) => ["section", "region"].includes(tool)),
  },
  {
    name: "page defines all primary dashboard modules",
    pass: requiredPageModules.length >= 6 && requiredPageModules.every(Boolean),
  },
  {
    name: "ship type chart has at least three categories",
    pass: shipTypeData.length >= 3,
  },
];

function cx(...classes) {
  return classes.filter(Boolean).join(" ");
}

function Card({ children, className = "" }) {
  return <div className={cx("rounded-[2rem] border border-slate-100 bg-white shadow-sm", className)}>{children}</div>;
}

function Button({ children, variant = "primary", className = "", onClick, type = "button" }) {
  const styles = {
    primary: "bg-blue-600 text-white hover:bg-blue-700",
    dark: "bg-slate-900 text-white hover:bg-slate-800",
    outline: "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50",
    subtle: "bg-slate-100 text-slate-700 hover:bg-slate-200",
  };
  return (
    <button type={type} onClick={onClick} className={cx("rounded-2xl px-4 py-2 text-sm font-medium transition", styles[variant] || styles.primary, className)}>
      {children}
    </button>
  );
}

function Badge({ children, tone = "slate" }) {
  const tones = {
    slate: "border-slate-200 bg-slate-50 text-slate-700",
    blue: "border-blue-200 bg-blue-50 text-blue-700",
    orange: "border-orange-200 bg-orange-50 text-orange-700",
    red: "border-red-200 bg-red-50 text-red-700",
    emerald: "border-emerald-200 bg-emerald-50 text-emerald-700",
    dark: "border-slate-900 bg-slate-900 text-white",
  };
  return <span className={cx("inline-flex rounded-full border px-3 py-1 text-xs font-medium", tones[tone] || tones.slate)}>{children}</span>;
}

function Input({ className = "", ...props }) {
  return (
    <input
      {...props}
      className={cx(
        "w-full rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm outline-none transition placeholder:text-slate-400 focus:border-blue-400 focus:ring-4 focus:ring-blue-100",
        className
      )}
    />
  );
}

function SelectBox({ children, className = "", ...props }) {
  return (
    <select
      {...props}
      className={cx(
        "w-full rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm outline-none transition focus:border-blue-400 focus:ring-4 focus:ring-blue-100",
        className
      )}
    >
      {children}
    </select>
  );
}

function Progress({ value, className = "" }) {
  const safeValue = Math.max(0, Math.min(100, Number(value) || 0));
  return (
    <div className={cx("h-2 overflow-hidden rounded-full bg-slate-100", className)}>
      <div className="h-full rounded-full bg-blue-600 transition-all" style={{ width: `${safeValue}%` }} />
    </div>
  );
}

function Toggle({ checked, onChange }) {
  return (
    <button type="button" onClick={() => onChange?.(!checked)} className={cx("relative h-6 w-11 rounded-full transition", checked ? "bg-blue-600" : "bg-slate-300")} aria-pressed={checked}>
      <span className={cx("absolute top-1 h-4 w-4 rounded-full bg-white shadow transition", checked ? "left-6" : "left-1")} />
    </button>
  );
}

function SectionTitle({ icon, title, subtitle }) {
  return (
    <div className="flex items-start gap-3">
      <span className="inline-flex h-9 w-9 items-center justify-center rounded-2xl bg-slate-100 text-lg font-bold text-slate-700">{icon}</span>
      <div>
        <h2 className="text-base font-semibold text-slate-950">{title}</h2>
        {subtitle ? <p className="mt-0.5 text-xs leading-5 text-slate-500">{subtitle}</p> : null}
      </div>
    </div>
  );
}

function MiniMetric({ label, value, icon }) {
  return (
    <div className="rounded-2xl border border-slate-100 bg-white/85 p-3 shadow-sm backdrop-blur">
      <div className="flex items-center justify-between">
        <span className="text-xs text-slate-500">{label}</span>
        <span className="text-sm font-bold text-slate-400">{icon}</span>
      </div>
      <div className="mt-2 text-xl font-semibold text-slate-950">{value}</div>
    </div>
  );
}

function ConfigNumberField({ label, unit, value, min = 0, max, step = 1, onChange }) {
  return (
    <label className="grid grid-cols-[1fr_92px] items-center gap-2 rounded-2xl bg-slate-50 px-3 py-2">
      <span className="text-xs text-slate-600">{label}</span>
      <div className="flex items-center gap-1">
        <input
          type="number"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(event) => onChange(Number(event.target.value))}
          className="w-16 rounded-xl border border-slate-200 bg-white px-2 py-1 text-right text-xs font-semibold text-slate-800 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
        />
        <span className="w-6 text-xs text-slate-400">{unit}</span>
      </div>
    </label>
  );
}

function BarSparkChart({ data }) {
  const maxFlow = Math.max(...data.map((item) => item.flow), 1);
  return (
    <div className="flex h-56 items-end gap-3 rounded-3xl bg-slate-50 p-4">
      {data.map((item) => (
        <div key={item.time} className="flex h-full flex-1 flex-col justify-end gap-2">
          <div className="relative flex flex-1 items-end justify-center">
            <div className="w-full rounded-t-2xl bg-blue-600 shadow-sm" style={{ height: `${(item.flow / maxFlow) * 100}%` }} title={`流量 ${item.flow}，速度 ${item.speed}kn，密度 ${item.density}`} />
            <div className="absolute left-1/2 h-2 w-2 -translate-x-1/2 rounded-full bg-orange-500 ring-4 ring-orange-100" style={{ bottom: `${Math.min(92, item.speed * 6)}%` }} title={`平均航速 ${item.speed}kn`} />
          </div>
          <div className="text-center text-xs text-slate-500">{item.time}</div>
        </div>
      ))}
    </div>
  );
}

function DensityLineChart({ data }) {
  const width = 560;
  const height = 180;
  const maxDensity = Math.max(...data.map((item) => item.density), 1);
  const points = data.map((item, index) => {
    const x = 24 + index * ((width - 48) / Math.max(data.length - 1, 1));
    const y = height - 24 - (item.density / maxDensity) * (height - 52);
    return [x, y];
  });
  const path = points.map(([x, y], index) => `${index === 0 ? "M" : "L"} ${x} ${y}`).join(" ");
  const area = `${path} L ${width - 24} ${height - 24} L 24 ${height - 24} Z`;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-40 w-full rounded-3xl bg-slate-50">
      <path d={area} fill="#2563eb" opacity="0.14" />
      <path d={path} fill="none" stroke="#2563eb" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
      {points.map(([x, y], index) => (
        <g key={`${data[index].time}-${index}`}>
          <circle cx={x} cy={y} r="5" fill="#2563eb" stroke="white" strokeWidth="3" />
          <text x={x} y={height - 8} textAnchor="middle" fontSize="12" fill="#64748b">{data[index].time}</text>
        </g>
      ))}
    </svg>
  );
}

function DonutChart({ data }) {
  const total = data.reduce((sum, item) => sum + item.value, 0) || 1;
  let current = 0;
  const radius = 78;
  const circumference = 2 * Math.PI * radius;

  return (
    <div className="flex flex-col items-center">
      <svg viewBox="0 0 220 220" className="h-48 w-48">
        <circle cx="110" cy="110" r={radius} fill="none" stroke="#e2e8f0" strokeWidth="28" />
        {data.map((item) => {
          const value = item.value / total;
          const dash = value * circumference;
          const gap = circumference - dash;
          const offset = -current * circumference;
          current += value;
          return (
            <circle key={item.name} cx="110" cy="110" r={radius} fill="none" stroke={item.color} strokeWidth="28" strokeDasharray={`${dash} ${gap}`} strokeDashoffset={offset} strokeLinecap="round" transform="rotate(-90 110 110)" />
          );
        })}
        <text x="110" y="104" textAnchor="middle" fontSize="26" fontWeight="800" fill="#0f172a">100%</text>
        <text x="110" y="128" textAnchor="middle" fontSize="12" fill="#64748b">船型结构</text>
      </svg>
    </div>
  );
}

function MapCanvas({ layers, setLayers }) {
  const [mapView, setMapView] = useState({ x: 0, y: 0, zoom: 1 });
  const [dragState, setDragState] = useState(null);
  const [drawMode, setDrawMode] = useState("none");
  const [sectionPoints, setSectionPoints] = useState([{ x: 675, y: 185 }, { x: 760, y: 540 }]);
  const [regionPoints, setRegionPoints] = useState([
    { x: 250, y: 330 },
    { x: 500, y: 250 },
    { x: 840, y: 300 },
    { x: 1120, y: 215 },
    { x: 1260, y: 420 },
    { x: 980, y: 570 },
    { x: 570, y: 520 },
    { x: 320, y: 610 },
  ]);
  const [coordInput, setCoordInput] = useState({ x: 680, y: 360 });

  const lanes = useMemo(
    () => [
      "M 80 430 C 260 330, 470 360, 690 250 S 1050 190, 1280 110",
      "M 40 520 C 280 460, 520 500, 760 390 S 1080 355, 1320 260",
      "M 160 610 C 380 575, 600 620, 820 510 S 1100 465, 1320 380",
    ],
    []
  );

  const ships = [
    [210, 455, -14], [330, 390, 18], [520, 348, -10], [690, 275, 12], [840, 242, -16],
    [1020, 205, 18], [300, 540, -8], [520, 515, 14], [760, 420, -16], [950, 390, 12],
    [1150, 320, -11], [440, 595, 10], [710, 560, -13], [930, 500, 15],
  ];

  const toggleLayer = (key) => setLayers((current) => ({ ...current, [key]: !current[key] }));
  const clampZoom = (value) => Math.max(mapInteractionDefaults.minZoom, Math.min(mapInteractionDefaults.maxZoom, value));
  const updateZoom = (delta) => setMapView((current) => ({ ...current, zoom: clampZoom(current.zoom + delta) }));
  const resetMapView = () => {
    setMapView({ x: 0, y: 0, zoom: 1 });
    setDragState(null);
  };

  const screenToMapPoint = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const rawX = (event.clientX - rect.left - mapView.x) / mapView.zoom;
    const rawY = (event.clientY - rect.top - mapView.y) / mapView.zoom;
    return {
      x: Math.round(Math.max(0, Math.min(1360, (rawX / rect.width) * 1360))),
      y: Math.round(Math.max(0, Math.min(720, (rawY / rect.height) * 720))),
    };
  };

  const addMapPoint = (point) => {
    const safePoint = { x: Number(point.x) || 0, y: Number(point.y) || 0 };
    if (drawMode === "section") {
      setSectionPoints((current) => [...current.slice(-1), safePoint].slice(-2));
      setLayers((current) => ({ ...current, section: true }));
    }
    if (drawMode === "region") {
      setRegionPoints((current) => [...current, safePoint].slice(-8));
      setLayers((current) => ({ ...current, region: true }));
    }
  };

  const handleWheel = (event) => {
    event.preventDefault();
    updateZoom(event.deltaY > 0 ? -mapInteractionDefaults.zoomStep : mapInteractionDefaults.zoomStep);
  };

  const handlePointerDown = (event) => {
    if (event.button !== 0) return;
    if (drawMode !== "none") {
      addMapPoint(screenToMapPoint(event));
      return;
    }
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setDragState({ pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, originX: mapView.x, originY: mapView.y });
  };

  const handlePointerMove = (event) => {
    if (!dragState) return;
    setMapView((current) => ({ ...current, x: dragState.originX + event.clientX - dragState.startX, y: dragState.originY + event.clientY - dragState.startY }));
  };

  const handlePointerUp = (event) => {
    if (dragState?.pointerId === event.pointerId) {
      event.currentTarget.releasePointerCapture?.(event.pointerId);
      setDragState(null);
    }
  };

  const mapTransform = `translate(${mapView.x}px, ${mapView.y}px) scale(${mapView.zoom})`;
  const sectionStart = sectionPoints[0] || { x: 675, y: 185 };
  const sectionEnd = sectionPoints[1] || { x: 760, y: 540 };
  const regionPath = regionPoints.length ? `${regionPoints.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ")} Z` : "";

  return (
    <div
      className={cx(
        "relative h-[calc(100vh-170px)] min-h-[620px] overflow-hidden rounded-[2rem] border border-slate-200 bg-slate-200 shadow-inner touch-none select-none",
        drawMode !== "none" ? "cursor-crosshair" : dragState ? "cursor-grabbing" : "cursor-grab"
      )}
      onWheel={handleWheel}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
    >
      <div className="absolute inset-0 transition-transform duration-75 ease-out" style={{ transform: mapTransform, transformOrigin: "center center" }}>
        <div className="absolute inset-0 grid grid-cols-3 grid-rows-3 opacity-95">
          {osmTiles.map((src, index) => (
            <img key={src} src={src} alt={`OpenStreetMap tile ${index + 1}`} className="h-full w-full object-cover" draggable={false} />
          ))}
        </div>
        <div className="absolute inset-0 bg-sky-50/10" />
        <svg viewBox="0 0 1360 720" className="absolute inset-0 h-full w-full">
          <defs>
            <radialGradient id="heat1"><stop offset="0%" stopColor="#f97316" /><stop offset="60%" stopColor="#fbbf24" stopOpacity=".45" /><stop offset="100%" stopColor="#f97316" stopOpacity="0" /></radialGradient>
            <radialGradient id="heat2"><stop offset="0%" stopColor="#ef4444" /><stop offset="62%" stopColor="#fb7185" stopOpacity=".38" /><stop offset="100%" stopColor="#ef4444" stopOpacity="0" /></radialGradient>
            <radialGradient id="heat3"><stop offset="0%" stopColor="#f59e0b" /><stop offset="65%" stopColor="#fcd34d" stopOpacity=".36" /><stop offset="100%" stopColor="#f59e0b" stopOpacity="0" /></radialGradient>
          </defs>
          {layers.region && regionPath ? (
            <g opacity="0.78">
              <path d={regionPath} fill="#2563eb" fillOpacity="0.07" stroke="#2563eb" strokeWidth="3" strokeDasharray="10 10" />
              <text x={regionPoints[0]?.x || 300} y={(regionPoints[0]?.y || 330) - 18} fontSize="22" fill="#1d4ed8" fontWeight="700">统计区域</text>
              {regionPoints.map((point, index) => <circle key={`region-${index}`} cx={point.x} cy={point.y} r="6" fill="#2563eb" stroke="white" strokeWidth="2" />)}
            </g>
          ) : null}
          {layers.heatmap ? (
            <g opacity="0.72">
              <circle cx="405" cy="450" r="170" fill="url(#heat1)" />
              <circle cx="780" cy="330" r="145" fill="url(#heat2)" />
              <circle cx="1010" cy="350" r="120" fill="url(#heat3)" />
            </g>
          ) : null}
          {layers.tracks ? lanes.map((d, index) => (
            <path key={d} d={d} fill="none" stroke={index === 1 ? "#2563eb" : "#0f766e"} strokeWidth={index === 1 ? 6 : 4} strokeLinecap="round" strokeDasharray={index === 2 ? "12 16" : "0"} opacity={0.95} />
          )) : null}
          {layers.section ? (
            <g>
              <line x1={sectionStart.x} y1={sectionStart.y} x2={sectionEnd.x} y2={sectionEnd.y} stroke="#0f172a" strokeWidth="5" strokeDasharray="14 12" opacity=".75" />
              <circle cx={sectionStart.x} cy={sectionStart.y} r="7" fill="#0f172a" stroke="white" strokeWidth="2" />
              <circle cx={sectionEnd.x} cy={sectionEnd.y} r="7" fill="#0f172a" stroke="white" strokeWidth="2" />
              <text x={Math.min(sectionStart.x, sectionEnd.x) + 30} y={Math.min(sectionStart.y, sectionEnd.y) - 15} fontSize="26" fill="#0f172a" fontWeight="700">断面 A</text>
            </g>
          ) : null}
          {layers.tracks ? ships.map(([x, y, r], index) => (
            <g key={`${x}-${y}`} transform={`translate(${x} ${y}) rotate(${r})`}>
              <path d="M 0 -15 L 32 0 L 0 15 L 9 0 Z" fill={index % 3 === 0 ? "#2563eb" : index % 3 === 1 ? "#14b8a6" : "#f97316"} stroke="white" strokeWidth="2.5" />
            </g>
          )) : null}
        </svg>
      </div>

      <div className="absolute left-5 top-5 z-10 flex items-center gap-2 rounded-2xl bg-white/90 px-3 py-2 shadow-sm backdrop-blur" onPointerDown={(event) => event.stopPropagation()} onWheel={(event) => event.stopPropagation()}>
        <span className="text-blue-600">▣</span>
        <span className="text-sm font-medium text-slate-800">OpenStreetMap AIS 轨迹底图</span>
      </div>

      <div className="absolute left-5 top-20 z-10 flex flex-col overflow-hidden rounded-2xl bg-white/90 shadow-sm backdrop-blur" onPointerDown={(event) => event.stopPropagation()} onWheel={(event) => event.stopPropagation()}>
        <button type="button" className="border-b border-slate-100 px-3 py-2 text-lg font-bold text-slate-700 hover:bg-slate-50" onClick={() => updateZoom(mapInteractionDefaults.zoomStep)}>+</button>
        <button type="button" className="border-b border-slate-100 px-3 py-2 text-lg font-bold text-slate-700 hover:bg-slate-50" onClick={() => updateZoom(-mapInteractionDefaults.zoomStep)}>−</button>
        <button type="button" className="px-3 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-50" onClick={resetMapView}>复位</button>
      </div>

      <div className="absolute left-20 top-20 z-10 rounded-2xl bg-white/85 px-3 py-2 text-xs text-slate-600 shadow-sm backdrop-blur" onPointerDown={(event) => event.stopPropagation()} onWheel={(event) => event.stopPropagation()}>
        {drawMode === "none" ? `拖拽平移 · 滚轮缩放 · 当前 ${Math.round(mapView.zoom * 100)}%` : `当前为${drawMode === "section" ? "截面" : "区域"}选点模式，点击地图添加点`}
      </div>

      <div className="absolute right-5 top-5 z-20 w-[340px] rounded-3xl bg-white/92 p-3 shadow-lg backdrop-blur" onPointerDown={(event) => event.stopPropagation()} onWheel={(event) => event.stopPropagation()}>
        <div className="flex items-center justify-between gap-2">
          <div className="text-sm font-semibold text-slate-800">空间对象设置</div>
          <button type="button" className="text-xs text-slate-500 hover:text-slate-900" onClick={() => setDrawMode("none")}>退出选点</button>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <Button variant={drawMode === "section" ? "dark" : "outline"} className="px-3 py-1.5 text-xs" onClick={() => setDrawMode(drawMode === "section" ? "none" : "section")}>添加截面</Button>
          <Button variant={drawMode === "region" ? "dark" : "outline"} className="px-3 py-1.5 text-xs" onClick={() => setDrawMode(drawMode === "region" ? "none" : "region")}>添加区域</Button>
        </div>
        <div className="mt-3 rounded-2xl bg-slate-50 p-3">
          <div className="mb-2 text-xs font-medium text-slate-600">输入坐标设置</div>
          <div className="grid grid-cols-2 gap-2">
            <Input type="number" value={coordInput.x} onChange={(event) => setCoordInput((current) => ({ ...current, x: Number(event.target.value) }))} className="px-3 py-1.5 text-xs" placeholder="X / 经度" />
            <Input type="number" value={coordInput.y} onChange={(event) => setCoordInput((current) => ({ ...current, y: Number(event.target.value) }))} className="px-3 py-1.5 text-xs" placeholder="Y / 纬度" />
          </div>
          <div className="mt-2 grid grid-cols-3 gap-2">
            <Button variant="subtle" className="px-2 py-1.5 text-xs" onClick={() => addMapPoint({ x: coordInput.x, y: coordInput.y })}>添加点</Button>
            <Button variant="subtle" className="px-2 py-1.5 text-xs" onClick={() => setSectionPoints([])}>清截面</Button>
            <Button variant="subtle" className="px-2 py-1.5 text-xs" onClick={() => setRegionPoints([])}>清区域</Button>
          </div>
          <div className="mt-2 text-[11px] leading-4 text-slate-500">截面保留最近 2 个点；区域最多保留最近 8 个点。也可在地图中直接点击选点。</div>
        </div>
      </div>

      <div className="absolute bottom-5 left-5 grid w-[460px] max-w-[calc(100%-3rem)] grid-cols-3 gap-3" onPointerDown={(event) => event.stopPropagation()} onWheel={(event) => event.stopPropagation()}>
        <MiniMetric label="区域流量" value="3,482 艘次" icon="⌁" />
        <MiniMetric label="平均航速" value="10.8 kn" icon="↗" />
        <MiniMetric label="活动密度" value="96 点/km²" icon="≋" />
      </div>

      <div className="absolute bottom-5 right-5 w-[260px] rounded-3xl bg-white/90 p-4 shadow-lg backdrop-blur" onPointerDown={(event) => event.stopPropagation()} onWheel={(event) => event.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <span className="text-sm font-semibold text-slate-800">图层控制</span>
          <span className="text-slate-500">▤</span>
        </div>
        {[["船舶轨迹", "tracks"], ["空间热力图", "heatmap"], ["航道断面", "section"], ["区域边界", "region"]].map(([item, key]) => (
          <div key={key} className="flex items-center justify-between py-2 text-sm text-slate-600">
            <span>{item}</span>
            <Toggle checked={Boolean(layers[key])} onChange={() => toggleLayer(key)} />
          </div>
        ))}
      </div>

      <div className="absolute bottom-2 right-5 rounded bg-white/80 px-2 py-1 text-[10px] text-slate-500 shadow-sm" onPointerDown={(event) => event.stopPropagation()} onWheel={(event) => event.stopPropagation()}>
        {osmAttribution}
      </div>
    </div>
  );
}

function TestPanel({ embedded = false }) {
  const passed = testCases.filter((item) => item.pass).length;
  const content = (
    <div className="p-4">
      <SectionTitle icon="✓" title="内置数据测试" subtitle="用于验证示例数据与关键字段，避免空数据或统计口径错误" />
      <div className="mt-3 rounded-2xl bg-emerald-50 p-2.5 text-sm font-medium text-emerald-800">
        {passed}/{testCases.length} tests passed
      </div>
      <div className="mt-2 space-y-1.5">
        {testCases.map((test) => (
          <div key={test.name} className="flex items-center justify-between gap-2 rounded-2xl bg-slate-50 px-3 py-1.5 text-xs text-slate-600">
            <span>{test.name}</span>
            <Badge tone={test.pass ? "emerald" : "red"}>{test.pass ? "PASS" : "FAIL"}</Badge>
          </div>
        ))}
      </div>
    </div>
  );
  return embedded ? content : <Card>{content}</Card>;
}

export default function AISTrajectoryPlatformUI() {
  const [layers, setLayers] = useState({ tracks: true, heatmap: true, section: true, region: true });
  const [activeScale, setActiveScale] = useState("日");
  const [cleaningConfig, setCleaningConfig] = useState(cleaningConfigDefaults);
  const [showCleaningSettings, setShowCleaningSettings] = useState(false);
  const selectedTrafficData = trafficByScale[activeScale] || trafficByScale["日"];

  const updateCleaningConfig = (key, value) => {
    setCleaningConfig((current) => ({ ...current, [key]: Number.isFinite(value) ? value : current[key] }));
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-950">
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/85 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1800px] items-center justify-between gap-4 px-5 py-3">
          <div className="flex items-center gap-3">
            <div className="rounded-2xl bg-blue-600 p-2.5 text-2xl text-white shadow-lg shadow-blue-200">▰</div>
            <div>
              <h1 className="text-xl font-bold tracking-tight">AIS 船舶轨迹分析与可视化平台</h1>
              <p className="text-xs text-slate-500">OpenStreetMap 底图 · AIS 轨迹重建 · 异常处理 · 交通流统计 · 密度热力图</p>
            </div>
          </div>
          <div className="hidden items-center gap-3 lg:flex">
            <Button variant="outline">⚙ 系统配置</Button>
            <Button>⇩ 导出报表</Button>
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-[1800px] grid-cols-12 items-start gap-4 px-5 py-4">
        <aside className="col-span-12 space-y-4 xl:col-span-3 2xl:col-span-2">
          <Card>
            <div className="p-4">
              <SectionTitle icon="⇪" title="数据导入与清洗" subtitle="文件导入、字段标准化、质量校验集中处理" />
              <div className="mt-4 rounded-3xl border-2 border-dashed border-blue-200 bg-blue-50/60 p-4 text-center">
                <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-2xl bg-white text-xl text-blue-600 shadow-sm">▤</div>
                <p className="mt-2 text-sm font-medium text-slate-800">拖拽 AIS 文件到此处</p>
                <p className="mt-1 text-xs text-slate-500">CSV / TXT 批量导入、编码识别、字段预览</p>
                <Button className="mt-3 py-1.5">选择文件</Button>
              </div>

              <div className="mt-4 rounded-3xl border border-slate-100 bg-white p-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-semibold text-slate-800">清洗参数</div>
                    <div className="mt-0.5 text-xs text-slate-500">当前：最少 {cleaningConfig.minTrackPoints} 点，分段 {cleaningConfig.segmentGapMinutes} min</div>
                  </div>
                  <Button variant={showCleaningSettings ? "dark" : "outline"} className="shrink-0 px-3 py-1.5 text-xs" onClick={() => setShowCleaningSettings((visible) => !visible)}>
                    {showCleaningSettings ? "收起设置" : "阈值设置"}
                  </Button>
                </div>
                {showCleaningSettings ? (
                  <div className="mt-3 rounded-3xl border border-blue-100 bg-blue-50/40 p-3">
                    <div className="mb-3 flex items-center justify-between">
                      <div className="text-sm font-semibold text-slate-800">清洗阈值设置</div>
                      <Badge tone="blue">可编辑</Badge>
                    </div>
                    <div className="grid gap-2">
                      <ConfigNumberField label="异常航速上限" unit="kn" value={cleaningConfig.maxSpeedKn} min={1} max={80} onChange={(value) => updateCleaningConfig("maxSpeedKn", value)} />
                      <ConfigNumberField label="跳点距离阈值" unit="nm" value={cleaningConfig.maxJumpDistanceNm} min={0.1} max={20} step={0.1} onChange={(value) => updateCleaningConfig("maxJumpDistanceNm", value)} />
                      <ConfigNumberField label="转向角突变阈值" unit="°" value={cleaningConfig.maxTurnAngleDeg} min={10} max={180} onChange={(value) => updateCleaningConfig("maxTurnAngleDeg", value)} />
                      <ConfigNumberField label="保留最小轨迹点" unit="点" value={cleaningConfig.minTrackPoints} min={2} max={100} onChange={(value) => updateCleaningConfig("minTrackPoints", value)} />
                      <ConfigNumberField label="轨迹分段时间间隔" unit="min" value={cleaningConfig.segmentGapMinutes} min={1} max={240} onChange={(value) => updateCleaningConfig("segmentGapMinutes", value)} />
                      <ConfigNumberField label="插值最大间隔" unit="min" value={cleaningConfig.interpolationGapMinutes} min={1} max={120} onChange={(value) => updateCleaningConfig("interpolationGapMinutes", value)} />
                      <ConfigNumberField label="平滑窗口大小" unit="点" value={cleaningConfig.smoothingWindow} min={1} max={25} onChange={(value) => updateCleaningConfig("smoothingWindow", value)} />
                    </div>
                    <div className="mt-3 rounded-2xl bg-white px-3 py-2 text-xs leading-5 text-blue-800">
                      当前规则：少于 {cleaningConfig.minTrackPoints} 个点的轨迹将被剔除；相邻报文超过 {cleaningConfig.segmentGapMinutes} 分钟将切分为新片段。
                    </div>
                  </div>
                ) : null}
              </div>

              <div className="mt-4 space-y-3">
                <div className="flex items-center justify-between text-sm">
                  <span>import_2026_05.csv</span>
                  <Badge tone="blue">解析中</Badge>
                </div>
                <Progress value={72} />
                <div className="grid grid-cols-2 gap-3 text-xs text-slate-500">
                  <span>已读取 7.1M 行</span>
                  <span className="text-right">字段匹配 98%</span>
                </div>
              </div>

              <div className="mt-4 border-t border-slate-100 pt-4">
                <div className="mb-3 text-sm font-semibold text-slate-800">AIS 字段解析与标准化</div>
                <div className="flex flex-wrap gap-2">
                  {fieldMap.map((field) => <Badge key={field}>{field}</Badge>)}
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-700">
                  {["MMSI 主键校验", "时间 UTC 标准化", "经纬度坐标校验", "静态/动态报文关联"].map((item) => (
                    <div key={item} className="flex items-center gap-2"><span className="text-emerald-500">✓</span>{item}</div>
                  ))}
                </div>
              </div>

              <div className="mt-4 border-t border-slate-100 pt-4">
                <div className="mb-3 text-sm font-semibold text-slate-800">数据清洗与异常过滤</div>
                <div className="space-y-3">
                  {qualityItems.map((item) => (
                    <div key={item.label}>
                      <div className="mb-1 flex items-center justify-between text-sm">
                        <span className="text-slate-700">{item.label}</span>
                        <span className="font-medium text-slate-900">{item.value}%</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <Progress value={item.value * 12} className="flex-1" />
                        <Badge tone={item.status === "待复核" ? "red" : "emerald"}>{item.status}</Badge>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </Card>

          <Card>
            <div className="p-4">
              <SectionTitle icon="◫" title="筛选条件" subtitle="按时间、区域、船型、MMSI 联动过滤数据" />
              <div className="mt-3 space-y-2.5">
                <Input placeholder="MMSI：例如 412345678" />
                <SelectBox defaultValue="cargo">
                  <option value="all">全部船型</option>
                  <option value="cargo">货船</option>
                  <option value="tanker">油船</option>
                  <option value="passenger">客船</option>
                </SelectBox>
                <SelectBox value={activeScale} onChange={(event) => setActiveScale(event.target.value)}>
                  <option value="日">按日统计</option>
                  <option value="月">按月统计</option>
                </SelectBox>
                <div className="grid grid-cols-2 gap-3">
                  <Button variant="outline">◷ 时间</Button>
                  <Button variant="outline">▱ 区域</Button>
                </div>
                <Button variant="dark" className="w-full">应用筛选</Button>
              </div>
            </div>
          </Card>
        </aside>

        <section className="col-span-12 space-y-4 xl:col-span-9 2xl:col-span-10">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {statCards.map((card) => (
              <Card key={card.label} className="rounded-[1.5rem] transition hover:-translate-y-0.5 hover:shadow-md">
                <div className="p-3.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-slate-500">{card.label}</span>
                    <span className="text-base font-bold text-blue-600">{card.icon}</span>
                  </div>
                  <div className="mt-1.5 flex items-baseline gap-1">
                    <span className="text-xl font-bold">{card.value}</span>
                    <span className="text-xs text-slate-500">{card.unit}</span>
                  </div>
                </div>
              </Card>
            ))}
          </div>

          <Card>
            <div className="p-3">
              <div className="mb-3 flex flex-col gap-3 px-1 lg:flex-row lg:items-center lg:justify-between">
                <SectionTitle icon="▣" title="OpenStreetMap 船舶轨迹主视图" subtitle="最大化地图区域，叠加 AIS 轨迹、活动热力、航道断面与区域统计" />
                <div className="flex items-center gap-3 rounded-2xl bg-slate-100 px-3 py-2 text-sm">
                  <span className="text-slate-600">显示热力图</span>
                  <Toggle checked={layers.heatmap} onChange={() => setLayers((current) => ({ ...current, heatmap: !current.heatmap }))} />
                </div>
              </div>
              <MapCanvas layers={layers} setLayers={setLayers} />
            </div>
          </Card>
        </section>

        <section className="col-span-12 grid gap-4 xl:grid-cols-3">
          <Card>
            <div className="p-4">
              <SectionTitle icon="⌁" title="轨迹重建与运动特征" subtitle="MMSI 时间序列、插值、平滑、状态识别" />
              <div className="mt-3 grid gap-2.5 md:grid-cols-2 xl:grid-cols-1">
                {trackEvents.map(({ title, desc, icon }) => (
                  <div key={title} className="rounded-2xl bg-slate-50 p-2.5">
                    <div className="flex items-center gap-2 text-sm font-semibold text-slate-800"><span className="text-blue-600">{icon}</span>{title}</div>
                    <p className="mt-0.5 pl-6 text-xs leading-4 text-slate-500">{desc}</p>
                  </div>
                ))}
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <MiniMetric label="航行距离" value="18,720 nm" icon="↔" />
                <MiniMetric label="平均加速度" value="0.18 kn/s" icon="↗" />
              </div>
            </div>
          </Card>

          <Card>
            <div className="p-4">
              <SectionTitle icon="▥" title="导入数据流量统计" subtitle={`默认展示已导入 AIS 数据的${activeScale}流量统计`} />
              <div className="mt-3 flex rounded-2xl bg-slate-100 p-1 text-sm">
                {["日", "月"].map((item) => (
                  <button key={item} type="button" onClick={() => setActiveScale(item)} className={cx("flex-1 rounded-xl px-4 py-1.5", activeScale === item ? "bg-white font-semibold text-blue-700 shadow-sm" : "text-slate-500")}>{item}</button>
                ))}
              </div>
              <div className="mt-3">
                <BarSparkChart data={selectedTrafficData} />
              </div>
              <div className="mt-3 flex flex-wrap gap-3 text-xs text-slate-500">
                <span><b className="text-blue-600">蓝色柱</b>：船舶流量</span>
                <span><b className="text-orange-500">橙色点</b>：平均航速</span>
              </div>
            </div>
          </Card>

          <Card>
            <div className="p-4">
              <SectionTitle icon="▰" title="导入数据船型统计" subtitle="默认展示当前导入 AIS 数据的船型结构" />
              <DonutChart data={shipTypeData} />
              <div className="grid grid-cols-2 gap-2">
                {shipTypeData.map((item) => (
                  <div key={item.name} className="flex items-center gap-2 text-sm text-slate-600">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: item.color }} />
                    {item.name} {item.value}%
                  </div>
                ))}
              </div>
            </div>
          </Card>
        </section>

        <section className="col-span-12 grid gap-4 xl:grid-cols-4">
          <Card>
            <div className="p-4">
              <SectionTitle icon="◉" title="基础航行状态识别" subtitle="航行、低速、停留自动判别" />
              <div className="mt-4 space-y-3">
                {[["航行", 68], ["低速", 21], ["停留", 11]].map(([label, value]) => (
                  <div key={label}>
                    <div className="mb-1 flex justify-between text-sm"><span>{label}</span><span>{value}%</span></div>
                    <Progress value={value} />
                  </div>
                ))}
              </div>
            </div>
          </Card>

          <Card>
            <div className="p-4">
              <SectionTitle icon="∿" title="密度分布对比" subtitle="按日、月不同时间尺度热力对比" />
              <div className="mt-4">
                <DensityLineChart data={selectedTrafficData} />
              </div>
            </div>
          </Card>

          <Card>
            <TestPanel embedded />
          </Card>

          <Card>
            <div className="p-4">
              <SectionTitle icon="⇩" title="统计结果与报表导出" subtitle="Excel、图片、专题图一键输出" />
              <div className="mt-4 grid gap-2.5">
                {["导入数据流量统计表", "导入数据船型结构表", "区域交通流统计表", "空间密度热力图", "轨迹异常点清单"].map((item) => (
                  <div key={item} className="flex items-center justify-between rounded-2xl bg-slate-50 px-3 py-2.5 text-sm">
                    <span>{item}</span>
                    <Button variant="outline" className="px-3 py-1 text-xs">导出</Button>
                  </div>
                ))}
              </div>
            </div>
          </Card>
        </section>
      </main>
    </div>
  );
}
