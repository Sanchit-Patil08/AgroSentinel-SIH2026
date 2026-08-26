const fieldGrid = document.getElementById("fieldGrid");

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function conditionClass(condition) {
  if (!condition) return "unknown";
  const c = condition.toLowerCase();
  if (c.includes("healthy")) return "healthy";
  if (c.includes("moderate")) return "moderate";
  if (c.includes("stress")) return "stressed";
  return "unknown";
}

function weatherStatusIcon(level) {
  if (level === "green") return "🟢";
  if (level === "yellow") return "🟡";
  return "⚪";
}

function buildWeatherStatusHtml(field) {
  const status = field.weather_status || { level: "grey", label: "No Data", message: "Weather data not available yet" };
  const weather = field.latest_weather;
  const temp = weather && weather.temperature_c !== null && weather.temperature_c !== undefined
    ? `${Math.round(weather.temperature_c)}°C`
    : "";

  return `
    <div class="weather-status ${escapeHtml(status.level)}">
      <span class="status-dot"></span>
      <span class="status-label">${weatherStatusIcon(status.level)} ${escapeHtml(status.label)}</span>
      ${temp ? `<span class="status-temp">${escapeHtml(temp)}</span>` : ""}
      <span class="status-msg">${escapeHtml(status.message)}</span>
    </div>
  `;
}

async function loadFields() {
  try {
    const res = await fetch("/api/fields");
    if (!res.ok) throw new Error("Failed to load fields.");
    const data = await res.json();
    renderFields(data.fields);
  } catch (err) {
    fieldGrid.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  }
}

function renderFields(fields) {
  let html = `
    <a class="add-field-card" href="/fields/new">
      <span class="plus">+</span>
      Add a new field
    </a>
  `;

  if (!fields.length) {
    fieldGrid.innerHTML = html;
    return;
  }

  fields.forEach((field) => {
    const latest = field.latest_analysis;
    const condition = latest ? latest.overall_condition : "Not analyzed yet";
    const pillClass = conditionClass(latest ? latest.overall_condition : null);
    const observedOn = latest ? latest.observation_date : "—";
    const ndvi = latest && latest.mean_ndvi !== null && latest.mean_ndvi !== undefined ? latest.mean_ndvi : "—";

    html += `
      <div class="field-card">
        <div class="top-row">
          <h3>${escapeHtml(field.name)}</h3>
          <button class="del-btn" title="Delete field" data-id="${field.id}" data-name="${escapeHtml(field.name)}">✕</button>
        </div>
        <span class="condition-pill ${pillClass}">${escapeHtml(condition)}</span>
        <div class="meta">
          <b>${escapeHtml(field.crop_type)}</b> · ${escapeHtml(field.crop_stage)}<br>
          Area: <b>${field.area_ha ?? "—"} ha</b> &nbsp;·&nbsp; Last observed: <b>${escapeHtml(observedOn)}</b><br>
          Mean NDVI: <b>${ndvi}</b>
        </div>
        ${buildWeatherStatusHtml(field)}
        <div class="actions">
          <a class="btn btn-ghost btn-sm" href="/fields/${field.id}">Open Field</a>
        </div>
      </div>
    `;
  });

  fieldGrid.innerHTML = html;

  document.querySelectorAll(".del-btn").forEach((btn) => {
    btn.addEventListener("click", () => deleteField(btn.dataset.id, btn.dataset.name));
  });
}

async function deleteField(id, name) {
  if (!confirm(`Delete "${name}"? This will permanently remove the field and all of its analysis history.`)) {
    return;
  }
  try {
    const res = await fetch(`/api/fields/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Failed to delete field.");
    loadFields();
  } catch (err) {
    alert(err.message);
  }
}

loadFields();