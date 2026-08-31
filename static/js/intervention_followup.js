// intervention_followup.js
// ================================================================
// Stage 4 continued: FOLLOW-UP -> RE-ANALYZE -> BEFORE/AFTER -> OUTCOME.
// Renders the "Interventions" tab. Follow-up re-analysis reuses the
// EXISTING field analysis pipeline via POST .../followup/run -- never
// a second analysis system.
// ================================================================

const interventionHistoryContentEl = document.getElementById("interventionHistoryContent");

const OUTCOME_ICON = {
  positive: "🟢", limited: "🟡", no_improvement: "🟠", worsened: "🔴", insufficient_data: "⚪",
};
const OUTCOME_LABEL = {
  positive: "Positive response detected",
  limited: "Mixed / limited response",
  no_improvement: "No clear improvement",
  worsened: "Field condition worsened",
  insufficient_data: "Not enough data to compare",
};
const SELECTED_OPTION_LABEL = {
  targeted: "🎯 Treat affected zones",
  field_wide: "🌾 Treat entire field",
  monitor: "👁 Monitor for now",
};

function followupStatusLabel(item) {
  if (item.status === "follow_up_completed") return "Completed";
  const due = item.follow_up_due_at ? new Date(item.follow_up_due_at) : null;
  return due && due <= new Date() ? "Due" : "Scheduled";
}

function snapshotRows(before, after) {
  const rows = [
    ["Stressed zones", before.stressed_zones, after.stressed_zones],
    ["Total zones", before.total_zones, after.total_zones],
    ["Mean NDVI", before.mean_ndvi, after.mean_ndvi],
    ["Risk level", before.risk_level, after.risk_level],
  ];
  return rows.map(([label, b, a]) => `
    <div class="interv-detail-row">
      <span>${escapeHtml(label)}</span>
      <span>${b != null ? escapeHtml(String(b)) : "—"} → ${a != null ? escapeHtml(String(a)) : "—"}</span>
    </div>
  `).join("");
}

function renderInterventionCard(item) {
  const statusLabel = followupStatusLabel(item);
  const recordedAt = new Date(item.created_at).toLocaleDateString();

  const headHtml = `
    <div class="interv-status-row">
      <span class="interv-status-pill">${escapeHtml(SELECTED_OPTION_LABEL[item.selected_option] || item.selected_option)}</span>
      <span class="interv-status-pill">${escapeHtml(statusLabel)}</span>
    </div>
    <p class="diag-caveat">Recorded ${escapeHtml(recordedAt)}${item.pesticide_use ? ` · ${escapeHtml(item.pesticide_use.insecticide)}` : ""}${item.affected_area_ha ? ` · ~${item.affected_area_ha} ha` : ""}</p>
  `;

  if (item.status === "follow_up_completed") {
    const outcome = item.outcome || "insufficient_data";
    return `
      <div class="fi-block interv-history-card">
        ${headHtml}
        <div class="interv-summary-box">
          <div class="diag-section-label">Before vs After</div>
          ${snapshotRows(item.before_snapshot || {}, item.after_snapshot || {})}
        </div>
        <div class="interv-outcome-banner interv-outcome-${outcome}">
          ${OUTCOME_ICON[outcome] || ""} <strong>${escapeHtml(OUTCOME_LABEL[outcome] || outcome)}</strong>
          <p>${escapeHtml(item.outcome_explanation || "")}</p>
        </div>
      </div>
    `;
  }

  return `
    <div class="fi-block interv-history-card">
      ${headHtml}
      <p class="diag-caveat">Follow-up window: ${item.follow_up_window_days_min}–${item.follow_up_window_days_max} days${item.follow_up_due_at ? ` (due ${new Date(item.follow_up_due_at).toLocaleDateString()})` : ""}.</p>
      <div class="diag-step-actions">
        <button class="btn btn-outline-soft btn-sm" data-run-followup="${item.id}">Run Follow-up Analysis</button>
      </div>
    </div>
  `;
}

async function loadInterventionHistory() {
  if (!interventionHistoryContentEl) return;
  interventionHistoryContentEl.innerHTML = `<div class="empty-state">Loading interventions…</div>`;

  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/intervention/history`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Could not load interventions.");

    const items = data.interventions || [];
    if (!items.length) {
      interventionHistoryContentEl.innerHTML = `<div class="empty-state">No interventions recorded yet.</div>`;
      return;
    }

    interventionHistoryContentEl.innerHTML = items.map(renderInterventionCard).join("");
    interventionHistoryContentEl.querySelectorAll("[data-run-followup]").forEach((btn) => {
      btn.addEventListener("click", () => runFollowup(btn.getAttribute("data-run-followup"), btn));
    });
  } catch (err) {
    interventionHistoryContentEl.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  }
}

async function runFollowup(interventionId, btn) {
  btn.disabled = true;
  btn.textContent = "Re-analyzing field…";
  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/intervention/${interventionId}/followup/run`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Follow-up analysis failed.");
    loadInterventionHistory();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Run Follow-up Analysis";
    alert(err.message);
  }
}

window.loadInterventionHistory = loadInterventionHistory;

document.addEventListener("DOMContentLoaded", () => {
  const tabBtn = document.querySelector('.nav-link[data-tab="interventions"]');
  if (tabBtn) tabBtn.addEventListener("click", loadInterventionHistory);
});

loadInterventionHistory();