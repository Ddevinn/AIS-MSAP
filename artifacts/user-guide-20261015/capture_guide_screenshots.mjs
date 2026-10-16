import fs from "node:fs";
import path from "node:path";
import { chromium } from "file:///C:/Users/Devin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core/index.mjs";

const baseUrl = "http://127.0.0.1:8000";
const outputDir = path.resolve("artifacts/user-guide-20261015/screenshots");
const sessionPath = "C:/Users/Devin/AppData/Local/Temp/ais-guide-session.json";
const session = JSON.parse(fs.readFileSync(sessionPath, "utf8"));
const chromePath = "C:/Program Files/Google/Chrome/Application/chrome.exe";

fs.mkdirSync(outputDir, { recursive: true });

const browser = await chromium.launch({
  executablePath: chromePath,
  headless: true,
  args: ["--disable-gpu", "--no-sandbox", "--font-render-hinting=none"],
});

async function captureElement(page, selector, filename) {
  const locator = page.locator(selector);
  await locator.scrollIntoViewIfNeeded();
  await page.waitForTimeout(500);
  await locator.screenshot({ path: path.join(outputDir, filename), animations: "disabled" });
}

async function captureGroup(page, selectors, filename) {
  const locators = selectors.map((selector) => page.locator(selector));
  await locators[0].scrollIntoViewIfNeeded();
  await page.waitForTimeout(700);
  const boxes = await Promise.all(locators.map((locator) => locator.boundingBox()));
  if (boxes.some((box) => !box)) throw new Error(`Unable to locate screenshot group: ${selectors.join(", ")}`);
  const x = Math.min(...boxes.map((box) => box.x));
  const y = Math.min(...boxes.map((box) => box.y));
  const right = Math.max(...boxes.map((box) => box.x + box.width));
  const bottom = Math.max(...boxes.map((box) => box.y + box.height));
  await page.screenshot({ path: path.join(outputDir, filename), clip: { x, y, width: right - x, height: bottom - y }, animations: "disabled" });
}

async function waitForWorkbench(page) {
  await page.waitForSelector("#currentDatasetName", { timeout: 60_000 });
  await page.waitForFunction(() => document.querySelector("#currentDatasetName")?.textContent?.trim() === "样例数据", null, { timeout: 60_000 });
  await page.waitForFunction(() => document.querySelector("#metrics")?.children?.length >= 6, null, { timeout: 60_000 });
  await page.waitForTimeout(4_000);
}

// 1. 登录入口：使用未登录的独立上下文，展示最新登录页文案与布局。
const loginContext = await browser.newContext({
  viewport: { width: 1680, height: 960 },
  deviceScaleFactor: 1,
  locale: "zh-CN",
  colorScheme: "light",
});
const loginPage = await loginContext.newPage();
await loginPage.goto(`${baseUrl}/login`, { waitUntil: "networkidle", timeout: 60_000 });
await loginPage.screenshot({ path: path.join(outputDir, "01-login.png"), fullPage: true, animations: "disabled" });
await loginContext.close();

const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  deviceScaleFactor: 1,
  locale: "zh-CN",
  colorScheme: "light",
});
await context.addCookies([{ name: "ais_session", value: session.token, url: baseUrl }]);
const page = await context.newPage();
await page.goto(baseUrl, { waitUntil: "commit", timeout: 120_000 });
await waitForWorkbench(page);

// 2. 新增文件：使用“样例数据”命名，并显示 Excel / CSV / PKL 统一选择入口。
await page.locator("#newDatasetBtn").click();
await page.locator("#datasetNameInput").fill("样例数据");
await captureElement(page, ".load-card", "02-add-dataset.png");

// 3. 数据清洗：展示样例数据的完成状态、质量摘要与默认参数入口。
await captureElement(page, ".cleaning-card", "03-cleaning.png");

// 4. 工作台总览：展示总览指标、轨迹、密度与地图工具。
await page.evaluate(() => {
  document.querySelector(".topbar").style.display = "none";
  document.querySelector("#newDatasetDetails").open = false;
  document.querySelectorAll(".side-card").forEach((card) => card.classList.remove("collapsed"));
  fitMapToData();
});
await page.waitForFunction(() => document.querySelector("#mapStatus")?.textContent?.includes("已更新"), null, { timeout: 120_000 });
await page.waitForTimeout(2_000);
await captureElement(page, ".map-card", "04-workbench-map.png");

const markers = [
  {
    id: 2026101501,
    type: "section",
    areaShape: null,
    name: "海口湾航道截面",
    category: "交通流统计",
    color: "#7c3aed",
    visible: true,
    points: [[109.90, 20.21], [110.45, 20.21]],
  },
  {
    id: 2026101502,
    type: "region",
    areaShape: "polygon",
    name: "海口湾重点水域",
    category: "交通流统计",
    color: "#ef476f",
    visible: true,
    points: [[109.95, 20.07], [110.24, 20.07], [110.24, 20.23], [109.95, 20.23]],
  },
];

await page.evaluate((items) => localStorage.setItem("ais-platform-markers:user_1", JSON.stringify(items)), markers);
await page.reload({ waitUntil: "commit", timeout: 120_000 });
await waitForWorkbench(page);
await page.evaluate(() => { document.querySelector(".topbar").style.display = "none"; });

// 5. 标记库：眼睛图标位于名称与编辑按钮之间；默认显示。
await page.evaluate(() => toggleMapTool("marker"));
await page.waitForTimeout(1_000);
await captureElement(page, ".map-card", "05-marker-visible.png");

// 6. 隐藏标记：切换区域标记的斜线眼睛状态，同时保留该标记供统计选择。
const regionRow = page.locator(".marker-item", { hasText: "海口湾重点水域" });
await regionRow.locator("button.visibility").click();
await page.waitForTimeout(800);
await captureElement(page, ".map-card", "06-marker-hidden.png");
await regionRow.locator("button.visibility").click();

// 7. 区域交通流：显示区域边界、区域内轨迹与交通统计。
await page.evaluate(() => {
  state.markers.find((marker) => marker.type === "section").visible = false;
  state.markers.find((marker) => marker.type === "region").visible = true;
  persistMarkers();
  renderMarkers();
  toggleMapTool("filter");
  document.querySelector("#spatialModeSelect").value = "region";
  updateSpatialMarkerOptions();
  document.querySelector("#spatialMarkerSelect").value = "2026101502";
});
await page.locator("#applySpatialBtn").click();
await page.waitForFunction(() => document.querySelector("#spatialFilterStatus")?.textContent?.includes("海口湾重点水域"), null, { timeout: 60_000 });
await page.waitForTimeout(4_000);
await captureElement(page, ".map-card", "07-region-traffic.png");

// 8. 截面交通流：显示截面、真实穿越轨迹及正反向统计。
await page.evaluate(() => {
  state.markers.find((marker) => marker.type === "section").visible = true;
  state.markers.find((marker) => marker.type === "region").visible = false;
  persistMarkers();
  renderMarkers();
  document.querySelector("#spatialModeSelect").value = "section";
  updateSpatialMarkerOptions();
  document.querySelector("#spatialMarkerSelect").value = "2026101501";
});
await page.locator("#applySpatialBtn").click();
await page.waitForFunction(() => document.querySelector("#spatialFilterStatus")?.textContent?.includes("海口湾航道截面"), null, { timeout: 60_000 });
await page.waitForTimeout(4_000);
await captureElement(page, ".map-card", "08-section-traffic.png");

// 9. 空间密度：清除空间条件，切换计数网格并展示图例。
await page.evaluate(async () => {
  await clearSpatialFilter();
  state.markers.forEach((marker) => { marker.visible = false; });
  persistMarkers();
  renderMarkers();
  toggleMapTool("density");
  document.querySelector('[data-density-mode="grid"]').click();
});
await page.waitForTimeout(4_000);
await captureElement(page, ".map-card", "09-density-grid.png");

// 10. 最新统计图表。
await page.locator("#directionMmsiInput").fill("353195000");
await page.locator("#applyDirectionMmsiBtn").click();
await page.waitForFunction(() => !document.querySelector("#directionTimeStatus")?.textContent?.includes("输入船舶编号"), null, { timeout: 60_000 });
await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
await page.waitForTimeout(1_500);
await captureElement(page, ".analysis-grid", "10-analysis-charts.png");
await captureGroup(page, ["#courseDirectionCard", "#headingDirectionCard"], "10a-direction-distributions.png");
await captureElement(page, "#directionTimeCard", "10b-direction-time.png");

// 11. 用户管理后台。
await page.goto(`${baseUrl}/admin`, { waitUntil: "networkidle", timeout: 60_000 });
await page.waitForSelector(".admin-table", { timeout: 60_000 });
await page.waitForTimeout(1_500);
await page.screenshot({ path: path.join(outputDir, "11-admin.png"), fullPage: true, animations: "disabled" });

console.log(`Captured 13 screenshots in ${outputDir}`);
await context.close();
await browser.close();
