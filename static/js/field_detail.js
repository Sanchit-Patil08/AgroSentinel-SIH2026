const map = L.map("map").setView([19.10, 72.85], 13);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

const boundaryLayer = L.featureGroup().addTo(map);
const zoneLayer = L.featureGroup().addTo(map);

const analyzeBtn = document.getElementById("analyzeBtn");
const analyzeError = document.getElementById("analyzeError");
const resultsArea = document.getElementById("resultsArea");
const loadingOverlay = document.getElementById("loadingOverlay");
const loadingText = document.getElementById("loadingText");

const weatherContent = document.getElementById("weatherContent");
const refreshWeatherBtn = document.getElementById("refreshWeatherBtn");
const weatherHistoryList = document.getElementById("weatherHistoryList");

const sensorContent = document.getElementById("sensorContent");
const simulateSensorBtn = document.getElementById("simulateSensorBtn");
const getSensorReadingBtn = document.getElementById("getSensorReadingBtn");
const riskContent = document.getElementById("riskContent");

const fiStatusBadge = document.getElementById("fiStatusBadge");
const evidenceBullets = document.getElementById("evidenceBullets");
const mlPredictionContent = document.getElementById("mlPredictionContent");
const fiConfidenceBlock = document.getElementById("fiConfidenceBlock");
const causeBars = document.getElementById("causeBars");
const nextStepContent = document.getElementById("nextStepContent");
const zoneTableEl = document.getElementById("zoneTable");
const evidenceZoneTableEl = document.getElementById("evidenceZoneTable");
const analysisHistoryEl = document.getElementById("analysisHistory");

// In-memory state used to progressively render the Overview tab as each
// independent fetch (analysis / risk / sensor) completes.
let fieldData = null;
let riskData = null;
let sensorData = null;

function drawBoundary(polygon) {
  boundaryLayer.clearLayers();
  const latLngs = polygon.map((p) => [p[1], p[0]]);
  const layer = L.polygon(latLngs, {
    color: "#16694c",
    weight: 2,
    fillOpacity: 0.05,
    dashArray: "6 4",
  });
  layer.addTo(boundaryLayer);
  const bounds = boundaryLayer.getBounds();
  if (bounds.isValid()) map.fitBounds(bounds, { padding: [30, 30] });
}

function renderEmptyResults() {
  resultsArea.innerHTML = `
    <div class="empty-state">
      This field hasn't been analyzed yet.<br><br>
      Click <strong>Analyze / Refresh Analysis</strong> above to run the satellite pipeline.
    </div>
  `;
}

function renderAnalysis(analysis, dataSource) {
  resultsArea.innerHTML = buildSummaryCardHtml(analysis, dataSource || analysis.data_source);
  const bounds = renderZoneLayer(map, zoneLayer, analysis.zones);
  if (bounds) map.fitBounds(bounds, { padding: [30, 30] });

  zoneTableEl.innerHTML = buildZoneListHtml(analysis.zones);
  evidenceZoneTableEl.innerHTML = buildEvidenceZoneTableHtml(analysis.zones);
  wireZoneRows();

  renderOverview();
}

function wireZoneRows() {
  zoneTableEl.querySelectorAll(".zone-table-row").forEach((row) => {
    row.addEventListener("click", () => {
      zoneTableEl.querySelectorAll(".zone-table-row").forEach((r) => r.classList.remove("selected"));
      row.classList.add("selected");
      switchTab("zones");
      focusZone(map, Number(row.dataset.zoneId));
    });
  });
}

/** Renders the synthesized Overview tab from whatever state is currently
 * available -- called after each of the three independent fetches
 * (analysis, risk, sensor) so the tab fills in progressively rather than
 * waiting on all three. */
function renderOverview() {
  const analysis = fieldData && fieldData.latest_analysis;

  const badge = buildStatusBadge(analysis, riskData);
  fiStatusBadge.outerHTML = `<span id="fiStatusBadge" class="status-badge ${badge.level}">${badge.html}</span>`;

  evidenceBullets.innerHTML = buildEvidenceBulletsHtml(analysis, riskData, sensorData);
  mlPredictionContent.innerHTML = buildMlPredictionHtml(riskData);
  causeBars.innerHTML = buildCauseBarsHtml(riskData);
  nextStepContent.innerHTML = buildNextStepHtml(riskData);

  if (riskData) {
    const confPct = Math.round((riskData.confidence ?? 0) * 100);
    fiConfidenceBlock.innerHTML = `
      <div class="confidence-block">
        <div class="confidence-label"><span>${confPct}% — ${confPct >= 75 ? "High" : confPct >= 50 ? "Medium" : "Low"}</span><span>${escapeHtml(formatDateTime(riskData.created_at))}</span></div>
        <div class="confidence-bar-track"><div class="confidence-bar-fill ${confidenceBarClass(riskData.confidence)}" style="width:${confPct}%"></div></div>
      </div>`;
  } else {
    fiConfidenceBlock.innerHTML = `<div class="empty-state">—</div>`;
  }
}

async function loadField() {
  try {
    const res = await fetch(`/api/fields/${FIELD_ID}`);
    if (!res.ok) throw new Error("Could not load this field.");
    const data = await res.json();
    fieldData = data.field;

    drawBoundary(fieldData.polygon);

    if (fieldData.latest_analysis) {
      renderAnalysis(fieldData.latest_analysis, fieldData.latest_analysis.data_source);
    } else {
      renderEmptyResults();
      renderOverview();
    }
  } catch (err) {
    resultsArea.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  }
}

// ---------------------------------------------------------- weather -----
function formatDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch (e) {
    return iso;
  }
}

function weatherStatusIcon(level) {
  if (level === "green") return "🟢";
  if (level === "yellow") return "🟡";
  return "⚪";
}

function renderCurrentWeather(weather) {
  if (!weather) {
    weatherContent.innerHTML = `<div class="empty-state">No weather data yet.</div>`;
    return;
  }
  const status = weather.status || {};
  const fmt = (v, unit) => (v === null || v === undefined ? "—" : `${v}${unit}`);

  weatherContent.innerHTML = `
    <div class="weather-status ${escapeHtml(status.level || "grey")}">
      <span class="status-dot"></span>
      <span class="status-label">${weatherStatusIcon(status.level)} ${escapeHtml(status.label || "")}</span>
      <span class="status-msg">${escapeHtml(status.message || "")}</span>
    </div>
    <div class="weather-grid">
      <div class="weather-stat"><span class="w-label">Temperature</span><span class="w-value">${fmt(weather.temperature_c, "°C")}</span></div>
      <div class="weather-stat"><span class="w-label">Feels Like</span><span class="w-value">${fmt(weather.feels_like_c, "°C")}</span></div>
      <div class="weather-stat"><span class="w-label">Humidity</span><span class="w-value">${fmt(weather.humidity_pct, "%")}</span></div>
      <div class="weather-stat"><span class="w-label">Rainfall</span><span class="w-value">${fmt(weather.precipitation_mm, " mm")}</span></div>
      <div class="weather-stat"><span class="w-label">Wind Speed</span><span class="w-value">${fmt(weather.wind_speed_kmh, " km/h")}</span></div>
      <div class="weather-stat"><span class="w-label">Condition</span><span class="w-value">${escapeHtml(weather.weather_description || weather.weather_condition || "—")}</span></div>
    </div>
    <div class="result-source">Last updated: ${escapeHtml(formatDateTime(weather.observed_at))} · source: ${escapeHtml(weather.source || "—")}</div>
  `;
}

function renderWeatherHistory(observations) {
  if (!observations || !observations.length) {
    weatherHistoryList.innerHTML = `<div class="empty-state">No weather history yet.</div>`;
    return;
  }
  weatherHistoryList.innerHTML = observations
    .map(
      (o) => `
    <div class="weather-history-row">
      <span class="wh-time">${escapeHtml(formatDateTime(o.observed_at))}</span>
      <span class="wh-detail">${o.temperature_c ?? "—"}°C · ${escapeHtml(o.weather_condition || "—")} · ${o.precipitation_mm ?? 0}mm rain</span>
    </div>
  `
    )
    .join("");
}

async function loadWeather() {
  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/weather`);
    if (!res.ok) throw new Error("Could not load weather.");
    const data = await res.json();
    renderCurrentWeather(data.weather);
  } catch (err) {
    weatherContent.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  }
}

async function loadWeatherHistory() {
  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/weather/history`);
    if (!res.ok) throw new Error("Could not load weather history.");
    const data = await res.json();
    renderWeatherHistory(data.observations);
  } catch (err) {
    weatherHistoryList.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  }
}

async function refreshWeather() {
  refreshWeatherBtn.disabled = true;
  const originalLabel = refreshWeatherBtn.textContent;
  refreshWeatherBtn.textContent = "Refreshing…";
  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/weather/refresh`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Could not refresh weather.");
    renderCurrentWeather(data.weather);
    loadWeatherHistory();
  } catch (err) {
    weatherContent.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  } finally {
    refreshWeatherBtn.disabled = false;
    refreshWeatherBtn.textContent = originalLabel;
  }
}

refreshWeatherBtn.addEventListener("click", refreshWeather);

// ------------------------------------------------------- IoT sensors -----
function renderSensor(reading) {
  sensorContent.innerHTML = renderSensorPanelHtml(reading);
  sensorData = reading;
  renderOverview();
}

async function loadSensor() {
  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/sensors`);
    if (!res.ok) throw new Error("Could not load sensor data.");
    const data = await res.json();
    renderSensor(data.reading);
  } catch (err) {
    sensorContent.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  }
}

async function simulateSensor(btn) {
  const target = btn || simulateSensorBtn;
  target.disabled = true;
  const originalLabel = target.textContent;
  target.textContent = "Simulating…";
  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/sensors/simulate`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Could not simulate a reading.");
    renderSensor(data.reading);
  } catch (err) {
    sensorContent.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  } finally {
    target.disabled = false;
    target.textContent = originalLabel;
  }
}

simulateSensorBtn.addEventListener("click", () => simulateSensor(simulateSensorBtn));
if (getSensorReadingBtn) {
  getSensorReadingBtn.addEventListener("click", () => {
    switchTab("evidence");
    simulateSensor(getSensorReadingBtn);
  });
}

// --------------------------------------------------- stress / risk -------
function renderRisk(risk) {
  riskContent.innerHTML = renderRiskPanelHtml(risk);
  riskData = risk;
  renderOverview();
}

async function loadRisk() {
  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/risk`);
    if (!res.ok) throw new Error("Could not load risk assessment.");
    const data = await res.json();
    renderRisk(data.risk);
  } catch (err) {
    riskContent.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  }
}

// -------------------------------------------------- analysis history -----
function conditionClass(condition) {
  if (!condition) return "unknown";
  const c = condition.toLowerCase();
  if (c.includes("healthy")) return "healthy";
  if (c.includes("moderate")) return "moderate";
  if (c.includes("stress")) return "stressed";
  return "unknown";
}

async function loadAnalysisHistory() {
  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/analyses`);
    if (!res.ok) throw new Error("Could not load analysis history.");
    const data = await res.json();
    if (!data.analyses || !data.analyses.length) {
      analysisHistoryEl.innerHTML = `<div class="empty-state">No analyses yet.</div>`;
      return;
    }
    analysisHistoryEl.innerHTML = data.analyses
      .map((a) => {
        const stats = a.zone_stats || {};
        return `
        <div class="history-row">
          <div>
            <span class="condition-pill ${conditionClass(a.overall_condition)}" style="font-size:0.7rem;padding:3px 10px;">${escapeHtml(a.overall_condition || "—")}</span>
            <span class="h-meta">&nbsp;${stats.stressed ?? 0}/${stats.total ?? 0} stressed · NDVI ${a.mean_ndvi ?? "—"}</span>
          </div>
          <span class="h-date">${escapeHtml(a.observation_date || "")}</span>
        </div>`;
      })
      .join("");
  } catch (err) {
    analysisHistoryEl.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  }
}

// -------------------------------------------------------------- tabs -----
function switchTab(name) {
  document.querySelectorAll("#fiTabs .nav-link").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  document.querySelectorAll(".fi-pane").forEach((pane) => {
    pane.style.display = pane.dataset.pane === name ? "" : "none";
  });
  if (name === "zones") {
    setTimeout(() => map.invalidateSize(), 50);
  }
}

document.querySelectorAll("#fiTabs .nav-link").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});
document.querySelectorAll("[data-tab-target]").forEach((el) => {
  el.addEventListener("click", (e) => {
    e.preventDefault();
    switchTab(el.dataset.tabTarget);
  });
});

// ----------------------------------------------------------- analyze -----
analyzeBtn.addEventListener("click", runAnalysis);

async function runAnalysis() {
  analyzeError.classList.remove("show");
  analyzeBtn.disabled = true;
  loadingOverlay.classList.remove("hidden");
  loadingText.textContent = "Retrieving satellite data…";
  resultsArea.innerHTML = `<div class="empty-state">Analyzing field…</div>`;
  switchTab("zones");

  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/analyze`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Analysis failed.");

    loadingText.textContent = "Rendering field-health zones…";
    fieldData = fieldData || {};
    fieldData.latest_analysis = data.analysis;
    renderAnalysis(data.analysis, data.analysis.data_source);

    // The analyze endpoint builds a fresh feature snapshot + risk
    // assessment right alongside the satellite analysis -- render it
    // directly from this response instead of a second round trip.
    renderRisk(data.analysis.risk);
    // A fresh analysis also refreshes the IoT panel (it ensures a
    // reading exists, simulating one if the field has no real sensors).
    loadSensor();
    loadAnalysisHistory();

    setTimeout(() => loadingOverlay.classList.add("hidden"), 250);
  } catch (err) {
    loadingOverlay.classList.add("hidden");
    analyzeError.textContent = err.message;
    analyzeError.classList.add("show");
    renderEmptyResults();
  } finally {
    analyzeBtn.disabled = false;
  }
}

loadField();
loadWeather();
loadWeatherHistory();
loadSensor();
loadRisk();
loadAnalysisHistory();