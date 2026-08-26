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

let fieldData = null;

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
      Click <strong>Analyze / Refresh Analysis</strong> to run the satellite pipeline.
    </div>
  `;
}

function renderAnalysis(analysis, dataSource) {
  resultsArea.innerHTML = buildSummaryCardHtml(analysis, dataSource || analysis.data_source);
  const bounds = renderZoneLayer(map, zoneLayer, analysis.zones);
  if (bounds) map.fitBounds(bounds, { padding: [30, 30] });
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
    }
  } catch (err) {
    resultsArea.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  }
}

// ---------------------------------------------------------- weather -----
function weatherStatusIcon(level) {
  if (level === "green") return "🟢";
  if (level === "yellow") return "🟡";
  return "⚪";
}

function formatDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch (e) {
    return iso;
  }
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

analyzeBtn.addEventListener("click", runAnalysis);

async function runAnalysis() {
  analyzeError.classList.remove("show");
  analyzeBtn.disabled = true;
  loadingOverlay.classList.remove("hidden");
  loadingText.textContent = "Retrieving satellite data…";
  resultsArea.innerHTML = `<div class="empty-state">Analyzing field…</div>`;

  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/analyze`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Analysis failed.");

    loadingText.textContent = "Rendering field-health zones…";
    renderAnalysis(data.analysis, data.analysis.data_source);

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