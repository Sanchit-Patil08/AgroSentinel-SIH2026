const fieldGrid = document.getElementById("fieldGrid");
const statStripEl = document.getElementById("statStrip");
const priorityAlertsEl = document.getElementById("priorityAlerts");
const inspectListEl = document.getElementById("inspectList");
const actionCountEl = document.getElementById("actionCount");
const greetingLineEl = document.getElementById("greetingLine");

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

function riskPillHtml(risk) {
  if (!risk) return "";
  const level = (risk.risk_level || "low").toLowerCase();
  const label = level.charAt(0).toUpperCase() + level.slice(1);
  return `<span class="risk-pill ${escapeHtml(level)}"><span class="dot"></span>${escapeHtml(label)} Risk</span>`;
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

function setGreeting() {
  if (!greetingLineEl) return;
  const hour = new Date().getHours();
  const timeOfDay = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  greetingLineEl.textContent = greetingLineEl.textContent.replace(/^Good \w+/, timeOfDay);
}

async function loadFields() {
  try {
    const res = await fetch("/api/fields");
    if (!res.ok) throw new Error("Failed to load fields.");
    const data = await res.json();
    renderFields(data.fields);
    renderCommandCenter(data.fields);
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
        ${field.latest_risk ? `<div class="risk-pill-row">${riskPillHtml(field.latest_risk)}</div>` : ""}
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

// ================================================================
// Command Center: priority alerts + ranked inspections + stat strip,
// all derived client-side from the same /api/fields payload the field
// cards already use -- no new backend endpoints required.
// ================================================================

function riskRank(level) {
  return { critical: 4, high: 3, moderate: 2, low: 1 }[level] || 0;
}

function fieldUrgency(field) {
  const risk = field.latest_risk;
  if (!field.latest_analysis) return { rank: 0, label: "Not yet analyzed" };
  if (!risk) return { rank: 1, label: "Analyzed, awaiting risk assessment" };
  const level = (risk.risk_level || "low").toLowerCase();
  const confPct = Math.round((risk.confidence ?? 0) * 100);
  const stats = field.latest_analysis.zone_stats || {};
  if (level === "critical" || level === "high") {
    return {
      rank: 10 + riskRank(level) + (1 - (risk.confidence ?? 0)),
      label: `${stats.stressed ?? 0}/${stats.total ?? 0} zones stressed · confidence ${confPct}%`,
      level,
    };
  }
  if (level === "moderate") {
    return { rank: 5, label: `Increasing stress signal · confidence ${confPct}%`, level };
  }
  return { rank: 1, label: "Stable", level: "low" };
}

function renderCommandCenter(fields) {
  setGreeting();

  const analyzed = fields.filter((f) => f.latest_analysis);
  const needsAttention = fields.filter((f) => {
    const level = f.latest_risk && (f.latest_risk.risk_level || "").toLowerCase();
    return level === "high" || level === "critical";
  });
  const stable = analyzed.filter((f) => {
    const level = f.latest_risk && (f.latest_risk.risk_level || "").toLowerCase();
    return !level || level === "low";
  });
  const notAnalyzed = fields.filter((f) => !f.latest_analysis);

  if (statStripEl) {
    const stats = statStripEl.querySelectorAll(".cc-stat");
    if (stats[0]) stats[0].querySelector(".n").textContent = fields.length;
    if (stats[1]) stats[1].querySelector(".n").textContent = needsAttention.length;
    if (stats[2]) stats[2].querySelector(".n").textContent = stable.length;
    if (stats[3]) stats[3].querySelector(".n").textContent = notAnalyzed.length;
  }

  renderPriorityAlerts(needsAttention);
  renderInspectionList(fields);

  if (actionCountEl) {
    const actionCount = needsAttention.length + fields.filter((f) => {
      const level = f.latest_risk && (f.latest_risk.risk_level || "").toLowerCase();
      return level === "moderate";
    }).length;
    actionCountEl.textContent = actionCount;
  }
}

function renderPriorityAlerts(fields) {
  if (!priorityAlertsEl) return;
  if (!fields.length) {
    priorityAlertsEl.innerHTML = `<div class="empty-state">Nothing urgent right now — all monitored fields are within normal range.</div>`;
    return;
  }

  const sorted = [...fields].sort((a, b) => riskRank(b.latest_risk.risk_level) - riskRank(a.latest_risk.risk_level));

  priorityAlertsEl.innerHTML = sorted
    .map((field) => {
      const risk = field.latest_risk;
      const level = (risk.risk_level || "high").toLowerCase();
      const stats = (field.latest_analysis && field.latest_analysis.zone_stats) || {};
      const confPct = Math.round((risk.confidence ?? 0) * 100);
      const causes = (risk.causes || []).slice(0, 2).map((c) => c.factor.replace(/_/g, " ")).join(", ");
      const topRec = (risk.recommendations || [])[0];

      return `
        <div class="cc-alert ${escapeHtml(level)}">
          <div>
            <span class="cc-alert-tag">${escapeHtml(level)} priority</span>
            <h4>${escapeHtml(field.name)}</h4>
            <p class="cc-alert-meta">${stats.stressed ?? "—"}/${stats.total ?? "—"} zones showing stress · Confidence: ${confPct}%</p>
            ${causes ? `<p class="cc-alert-causes">Possible causes: <b>${escapeHtml(causes)}</b></p>` : ""}
            ${topRec ? `<p class="cc-alert-causes">Recommended: <b>${escapeHtml(topRec.title)}</b></p>` : ""}
          </div>
          <a class="btn btn-primary btn-sm cc-alert-cta" href="/fields/${field.id}">Inspect Field →</a>
        </div>`;
    })
    .join("");
}

function renderInspectionList(fields) {
  if (!inspectListEl) return;
  if (!fields.length) {
    inspectListEl.innerHTML = `<li class="empty-state">No fields yet — add your first field to get started.</li>`;
    return;
  }

  const ranked = fields
    .map((f) => ({ field: f, urgency: fieldUrgency(f) }))
    .sort((a, b) => b.urgency.rank - a.urgency.rank);

  inspectListEl.innerHTML = ranked
    .map(({ field, urgency }, i) => {
      let tag = `<span class="urgency-tag none">No inspection needed</span>`;
      if (!field.latest_analysis) tag = `<span class="urgency-tag h24">Run first analysis</span>`;
      else if (urgency.level === "critical" || urgency.level === "high") tag = `<span class="urgency-tag today">Inspect today</span>`;
      else if (urgency.level === "moderate") tag = `<span class="urgency-tag h24">Within 24h</span>`;

      return `
        <a class="cc-inspect-row" href="/fields/${field.id}">
          <span class="cc-rank">${i + 1}</span>
          <span style="flex:1;">
            <span class="cc-inspect-name">${escapeHtml(field.name)}</span><br>
            <span class="cc-inspect-reason">${escapeHtml(urgency.label)}</span>
          </span>
          ${tag}
        </a>`;
    })
    .join("");
}

loadFields();