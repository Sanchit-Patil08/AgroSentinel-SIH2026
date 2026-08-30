// Shared rendering helpers for the intelligence layer: the IoT sensor
// panel and the stress/risk panel shown on the field-detail page.
// Mirrors the pattern of zone_render.js (markup builders that field_detail.js
// wires up to fetch results). Relies on escapeHtml() from zone_render.js.

function formatSensorValue(v, unit, decimals) {
  if (v === null || v === undefined) return "—";
  const num = typeof decimals === "number" ? Number(v).toFixed(decimals) : v;
  return `${num}${unit}`;
}

/** Renders the "Current Sensor Readings" card content. */
function renderSensorPanelHtml(reading) {
  if (!reading) {
    return `<div class="empty-state">No sensor data yet.</div>`;
  }
  const isSimulated = reading.source === "simulated";
  const tagLabel = isSimulated ? "Simulated (no device yet)" : `Device: ${reading.sensor_id || reading.source}`;

  const stat = (label, value, unit, decimals, warn) => `
    <div class="sensor-stat ${warn ? "warn" : ""}">
      <span class="s-label">${escapeHtml(label)}</span>
      <span class="s-value">${formatSensorValue(value, unit, decimals)}</span>
    </div>`;

  const lowMoisture = reading.soil_moisture_pct !== null && reading.soil_moisture_pct !== undefined && reading.soil_moisture_pct < 20;

  return `
    <div class="sensor-source-tag ${isSimulated ? "simulated" : ""}">${escapeHtml(tagLabel)}</div>
    <div class="sensor-grid">
      ${stat("Soil Moisture", reading.soil_moisture_pct, "%", 1, lowMoisture)}
      ${stat("Soil Temp", reading.soil_temperature_c, "°C", 1)}
      ${stat("Soil pH", reading.soil_ph, "", 2)}
      ${stat("Soil EC", reading.soil_ec_ds_m, " dS/m", 2)}
      ${stat("Leaf Wetness", reading.leaf_wetness_pct, "%", 1)}
      ${stat("Air Temp", reading.air_temperature_c, "°C", 1)}
      ${stat("Air Humidity", reading.air_humidity_pct, "%", 1)}
      ${stat("Nitrogen (N)", reading.soil_nitrogen_ppm, " ppm", 1)}
      ${stat("Phosphorus (P)", reading.soil_phosphorus_ppm, " ppm", 1)}
      ${stat("Potassium (K)", reading.soil_potassium_ppm, " ppm", 1)}
      ${stat("Rain Gauge", reading.rainfall_mm, " mm", 1)}
      ${stat("Battery", reading.battery_pct, "%", 0)}
    </div>
    <div class="result-source">Last reading: ${escapeHtml(formatDateTime(reading.observed_at))}${reading.zone_label ? " · " + escapeHtml(reading.zone_label) : ""}</div>
  `;
}

function priorityPillHtml(priority) {
  const p = (priority || "low").toLowerCase();
  return `<span class="priority-pill ${escapeHtml(p)}">${escapeHtml(p)}</span>`;
}

function confidenceBarClass(confidence) {
  if (confidence === null || confidence === undefined) return "";
  if (confidence < 0.4) return "very-low";
  if (confidence < 0.65) return "low";
  return "";
}

/** Renders the "Stress / Risk Analysis" card content. */
function renderRiskPanelHtml(risk) {
  if (!risk) {
    return `<div class="empty-state">No risk assessment yet. Run <strong>Analyze / Refresh Analysis</strong> to generate one.</div>`;
  }

  const level = (risk.risk_level || "low").toLowerCase();
  const scorePct = risk.risk_score !== null && risk.risk_score !== undefined ? Math.round(risk.risk_score * 100) : null;
  const confPct = risk.confidence !== null && risk.confidence !== undefined ? Math.round(risk.confidence * 100) : null;

  const causesHtml = (risk.causes && risk.causes.length)
    ? risk.causes.map((c) => `
        <div class="cause-item">
          <span class="cause-factor">${escapeHtml(prettifyFactor(c.factor))}</span>
          ${escapeHtml(c.detail)}
        </div>`).join("")
    : `<div class="empty-state" style="padding:6px 0;">No significant stress signals detected.</div>`;

  const recsHtml = (risk.recommendations && risk.recommendations.length)
    ? risk.recommendations.map((r) => `
        <div class="rec-item">
          <span class="rec-title">${escapeHtml(r.title)} ${priorityPillHtml(r.priority)}</span>
          <span>${escapeHtml(r.detail)}</span>
        </div>`).join("")
    : "";

  return `
    <div class="risk-level-badge ${escapeHtml(level)}">
      <span class="dot"></span>
      ${escapeHtml(level.charAt(0).toUpperCase() + level.slice(1))} Risk
    </div>
    <div class="risk-score-row">
      <span>Risk score: ${scorePct === null ? "—" : scorePct + "%"}</span>
      <span>${escapeHtml(formatDateTime(risk.created_at))}</span>
    </div>

    <div class="confidence-block">
      <div class="confidence-label"><span>Confidence</span><span>${confPct === null ? "—" : confPct + "%"}</span></div>
      <div class="confidence-bar-track"><div class="confidence-bar-fill ${confidenceBarClass(risk.confidence)}" style="width:${confPct ?? 0}%"></div></div>
    </div>

    <div class="risk-section-title">Possible Causes</div>
    <div class="cause-list">${causesHtml}</div>

    ${recsHtml ? `<div class="risk-section-title">Recommended Actions</div><div class="rec-list">${recsHtml}</div>` : ""}

    <div class="result-source">Method: ${escapeHtml(risk.method || "—")}</div>
  `;
}

// ------------------------------------------------------------------------
// Field Intelligence overview helpers (dashboard-style synthesis built
// entirely from data the existing endpoints already return: the latest
// Analysis' zone_stats/overall_condition and the latest RiskAssessment's
// risk_level/confidence/causes/recommendations).
// ------------------------------------------------------------------------

function statusLevelFromRisk(risk, analysis) {
  if (risk && risk.risk_level) return risk.risk_level.toLowerCase();
  if (analysis && analysis.overall_condition) {
    const c = analysis.overall_condition.toLowerCase();
    if (c.includes("stress")) return "high";
    if (c.includes("moderate")) return "moderate";
    if (c.includes("healthy")) return "healthy";
  }
  return "unknown";
}

function statusLabel(level) {
  return {
    unknown: "Not analyzed",
    healthy: "Healthy",
    low: "Healthy",
    moderate: "Moderate",
    high: "High Attention",
    critical: "Critical",
  }[level] || "Unknown";
}

/** Header status badge on the Field Intelligence page: { level, html } so
 * the caller can set both the class and the inner content in place. */
function buildStatusBadge(analysis, risk) {
  const rawLevel = statusLevelFromRisk(risk, analysis);
  const level = rawLevel === "low" ? "healthy" : rawLevel;
  const stats = analysis ? analysis.zone_stats : null;
  const zoneLine = stats
    ? `${stats.stressed}/${stats.total} zones stressed`
    : "No analysis yet";
  const html = `FIELD STATUS<br><strong>${escapeHtml(statusLabel(level))}</strong><br><span style="font-weight:400;text-transform:none;font-size:0.72rem;">${escapeHtml(zoneLine)}</span>`;
  return { level, html };
}

/** "Why is this field flagged?" evidence bullet list on the Overview tab. */
function buildEvidenceBulletsHtml(analysis, risk, sensor) {
  const bullets = [];

  if (analysis) {
    const stats = analysis.zone_stats || {};
    if (stats.stressed > 0) {
      bullets.push(`Satellite shows ${stats.stressed} of ${stats.total} zones classified as stressed (mean NDVI ${analysis.mean_ndvi ?? "—"})`);
    } else if (stats.total) {
      bullets.push(`Satellite shows all ${stats.total} zones within a healthy/moderate NDVI range`);
    }
  }

  if (risk && risk.causes && risk.causes.length) {
    risk.causes.slice(0, 3).forEach((c) => bullets.push(`${prettifyFactor(c.factor)}: ${c.detail}`));
  }

  if (sensor === null) {
    bullets.push("No physical sensor connected — assessment relies on satellite + weather evidence");
  } else if (sensor && sensor.source === "simulated") {
    bullets.push("Sensor reading is simulated, not a physical device — treat as lower-confidence evidence");
  }

  if (!bullets.length) {
    return `<li class="empty-state">Run an analysis to see evidence.</li>`;
  }
  return bullets.map((b) => `<li>${escapeHtml(b)}</li>`).join("");
}

/** Horizontal likely-causes bars, built from risk.causes[].weight. */
function buildCauseBarsHtml(risk) {
  if (!risk || !risk.causes || !risk.causes.length) {
    return `<div class="empty-state">No causes identified yet.</div>`;
  }
  const causes = [...risk.causes].sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0));
  const total = causes.reduce((sum, c) => sum + (c.weight ?? 0), 0) || 1;
  return causes
    .map((c) => {
      const pct = Math.round(((c.weight ?? 0) / total) * 100);
      return `
        <div class="cause-bar-row">
          <span class="cbr-label">${escapeHtml(prettifyFactor(c.factor))}</span>
          <span class="cbr-track"><span class="cbr-fill" style="width:${pct}%"></span></span>
          <span class="cbr-value">${pct}%</span>
        </div>`;
    })
    .join("");
}

/** "Recommended Next Step" block — the "do not spray yet" gate. */
function buildNextStepHtml(risk) {
  if (!risk) {
    return `<div class="empty-state">Run an analysis to get a recommendation.</div>`;
  }

  const level = (risk.risk_level || "low").toLowerCase();
  const conf = risk.confidence ?? 0;
  const confPct = Math.round(conf * 100);

  const recSteps = (risk.recommendations || [])
    .map((r) => `<li><strong>${escapeHtml(r.title)}</strong> — ${escapeHtml(r.detail)}</li>`)
    .join("");

  if (level === "low" && !recSteps) {
    return `
      <div class="fi-clear">
        ✓ No significant stress detected — continue routine monitoring.
      </div>
    `;
  }

  if (level === "low" && recSteps) {
    return `
      <div class="fi-clear">
        ✓ Low overall risk — no immediate treatment recommended. Follow the monitoring and inspection steps below.
      </div>
      <ol>${recSteps}</ol>
    `;
  }

  if (conf < 0.7) {
    return `
      <div class="fi-caution ${level === "critical" ? "critical" : ""}">
        <span class="fi-caution-title">⚠ Do not spray yet</span>
        <span class="fi-caution-body">
          The system detects crop stress (confidence ${confPct}%), but the exact cause is not yet confirmed. Inspect before treating.
        </span>
      </div>
      ${recSteps ? `<ol>${recSteps}</ol>` : `
        <ol>
          <li>Inspect the affected zone</li>
          <li>Check leaves for visible symptoms</li>
          <li>Upload an image if symptoms are present</li>
          <li>Collect a sensor reading if available</li>
        </ol>
      `}
    `;
  }

  return `
    <div class="fi-caution ${level === "critical" ? "critical" : ""}">
      <span class="fi-caution-title">${level === "critical" ? "Act now" : "Attention recommended"}</span>
      <span class="fi-caution-body">
        Confidence ${confPct}% — evidence is fairly consistent. Confirm on the ground before treating.
      </span>
    </div>
    ${recSteps ? `<ol>${recSteps}</ol>` : ""}
  `;
}

/** Renders the "AI Stress Prediction" block on the Overview tab from
 * risk.ml_prediction (see backend/services/ml_risk_model.py). This is a
 * PREDICTION only -- a probability + level badge -- deliberately separate
 * from the rule-based causes/recommendations shown elsewhere on the tab. */
function buildMlPredictionHtml(risk) {
  const ml = risk && risk.ml_prediction;

  if (!ml) {
    return `<div class="empty-state">Run an analysis to get a prediction.</div>`;
  }

  if (!ml.available) {
    return `
      <div class="empty-state">
        ${escapeHtml(ml.note || "ML model not trained yet.")}
      </div>`;
  }

  const level = (ml.risk_level || "low").toLowerCase();
  const pct = Math.round((ml.stress_probability ?? 0) * 100);
  const metrics = ml.holdout_metrics;
  const metricsLine = metrics
    ? `Validated on held-out data: MAE ${metrics.mae}, R² ${metrics.r2} (n=${metrics.n_test})`
    : null;

  const missingLine = (ml.features_missing && ml.features_missing.length)
    ? `<div class="ml-note">${escapeHtml(ml.note || "")}</div>`
    : "";

  return `
    <div class="risk-level-badge ${escapeHtml(level)}">
      <span class="dot"></span>
      ${pct}% stress probability
    </div>
    <div class="result-source" style="margin-top:6px;">
      Model: ${escapeHtml(ml.model_version || "—")}${ml.trained_on ? " · trained on " + escapeHtml(ml.trained_on) : ""}
    </div>
    ${metricsLine ? `<div class="result-source">${escapeHtml(metricsLine)}</div>` : ""}
    ${missingLine}
  `;
}

function prettifyFactor(factor) {
  if (!factor) return "Signal";
  return factor.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Small risk badge used on the dashboard's field cards. */
function riskPillHtml(risk) {
  if (!risk) return "";
  const level = (risk.risk_level || "low").toLowerCase();
  const label = level.charAt(0).toUpperCase() + level.slice(1);
  return `<span class="risk-pill ${escapeHtml(level)}"><span class="dot"></span>${escapeHtml(label)} Risk</span>`;
}