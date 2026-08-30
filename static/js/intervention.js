// "Recommended Intervention" panel + Intervention Simulator modal
// (Stage 3 of the field-intelligence page, after Analysis and Diagnosis).
//
// This module is FIELD-SPECIFIC decision support, separate from the
// general "Pest & Disease" / "Pesticide Advisor" nav placeholders --
// see backend/routes/intervention.py's module docstring. It only ever
// reads from:
//   GET  /api/fields/<id>/intervention/context
//   GET  /api/fields/<id>/intervention/options
//   POST /api/fields/<id>/intervention/simulate
//
// The MONITOR / VERIFY / TARGETED / FIELD_WIDE decision cascade lives
// entirely in backend/services/intervention_engine.py. This file never
// re-derives or second-guesses that decision -- it only renders the
// `pathway` / `intervention_appropriate` / `matches` fields the backend
// already computed. Per the project brief, approved-use pesticide
// options (and the Simulator) are only ever shown when
// `intervention_appropriate` is true; a MONITOR/VERIFY result is always
// rendered as guidance text only, never as a pesticide recommendation.
//
// Reuses escapeHtml() from zone_render.js and the FIELD_ID global from
// field_detail.html, same as diagnosis.js. Exposes window.refreshIntervention
// so diagnosis.js can ask this panel to reload itself once a diagnosis
// completes (see the hook at the bottom of diagnosis.js's submitDiagnosis()).

const interventionContentEl = document.getElementById("interventionContent");
const intervSimModalEl = document.getElementById("interventionSimModal");
const intervSimModalBody = document.getElementById("intervSimModalBody");

let bsIntervSimModal = null;
let currentInterventionContext = null;
let currentIntervention = null;
let currentInterventionMatches = [];

function getBsIntervSimModal() {
  if (!bsIntervSimModal) {
    bsIntervSimModal = new bootstrap.Modal(intervSimModalEl);
  }
  return bsIntervSimModal;
}

const PATHWAY_LABELS = {
  monitor: "Monitor",
  verify: "Verify Needed",
  targeted: "Targeted Treatment",
  field_wide_available: "Field-Wide Option Available",
};

function pathwayLabel(pathway) {
  return PATHWAY_LABELS[pathway] || (pathway ? pathway.replace(/_/g, " ") : "—");
}

// ------------------------------------------------------- render panel ---
function renderIntervention(context, intervention) {
  if (!interventionContentEl) return;

  if (!intervention) {
    interventionContentEl.innerHTML = `<div class="empty-state">Could not load intervention guidance.</div>`;
    return;
  }

  const diag = context && context.diagnosis;
  const zones = context && context.affected_zones;

  const urgency = intervention.urgency || "none";
  const urgencyClass =
    urgency === "high" || urgency === "medium"
      ? `interv-urgency-${urgency}`
      : "";

  const statusRowHtml = `
    <div class="interv-status-row">
      <span class="interv-status-pill">
        ${escapeHtml(pathwayLabel(intervention.pathway))}
      </span>
      ${
        urgency !== "none"
          ? `<span class="interv-urgency ${urgencyClass}">
              ${escapeHtml(urgency)} urgency
            </span>`
          : ""
      }
    </div>
  `;

  const issueHtml =
    diag && diag.possible_cause
      ? `<p class="interv-possible-issue">
          Possible issue: ${escapeHtml(diag.possible_cause)}
        </p>`
      : `<p class="interv-possible-issue">
          No completed diagnosis on file yet.
        </p>`;

  const areaBits = [];

  if (zones && zones.total) {
    areaBits.push(`${zones.stressed}/${zones.total} zones affected`);
  }

  if (intervention.affected_area_ha != null) {
    areaBits.push(`~${intervention.affected_area_ha} ha`);
  }

  const zonesHtml =
    areaBits.length
      ? `<p class="interv-zones">
          ${escapeHtml(areaBits.join(" · "))}
          ${
            intervention.affected_area_basis
              ? ` <span class="diag-caveat">
                  (${escapeHtml(intervention.affected_area_basis)})
                </span>`
              : ""
          }
        </p>`
      : "";

  const reasonsHtml =
    intervention.reasons && intervention.reasons.length
      ? `
        <div class="diag-section-label">Why this status</div>
        <ul class="diag-list">
          ${intervention.reasons
            .map((r) => `<li>${escapeHtml(r)}</li>`)
            .join("")}
        </ul>
      `
      : "";

  const warningsHtml =
    intervention.warnings && intervention.warnings.length
      ? `
        <div class="interv-warnings">
          <div class="diag-section-label">Warnings</div>
          <ul class="diag-list">
            ${intervention.warnings
              .map((w) => `<li>${escapeHtml(w)}</li>`)
              .join("")}
          </ul>
        </div>
      `
      : "";

  const confidenceNoteHtml = intervention.confidence_note
    ? `<p class="interv-confidence-note">
        ${escapeHtml(intervention.confidence_note)}
      </p>`
    : "";

  const historyHtml = intervention.history_note
    ? `<p class="interv-confidence-note">
        ${escapeHtml(intervention.history_note)}
      </p>`
    : "";

  let matchesHtml = "";
  let simButtonHtml = "";

  if (intervention.matches && intervention.matches.length) {
    currentInterventionMatches = intervention.matches;

    const matchHeading = intervention.intervention_appropriate
      ? "Approved-use options for this field"
      : "Approved-use records for verification";

    matchesHtml = `
      <div class="diag-section-label">${matchHeading}</div>

      <div class="interv-table-wrap">
        <table class="interv-table">
          <thead>
            <tr>
              <th>Pesticide</th>
              <th>Crop match</th>
              <th>Pest match</th>
              <th>Dosage (a.i./ha)</th>
              <th>Formulation</th>
              <th>Spray fluid (L/ha)</th>
              <th>Source</th>
            </tr>
          </thead>

          <tbody>
            ${intervention.matches
              .map(
                (m) => `
                  <tr>
                    <td>${escapeHtml(m.insecticide || "—")}</td>
                    <td>${escapeHtml(m.crop || "—")}</td>
                    <td>${escapeHtml(m.pest || "—")}</td>
                    <td>${escapeHtml(m.dosage_ai_gm_ha || "—")}</td>
                    <td>${escapeHtml(m.formulation_dosage || "—")}</td>
                    <td>${escapeHtml(m.spray_fluid || "—")}</td>
                    <td>${escapeHtml(m.source || "—")}</td>
                  </tr>
                `
              )
              .join("")}
          </tbody>
        </table>
      </div>

      <p class="diag-caveat">
        These records were matched using this field's crop and suspected pest
        category. They are not a treatment prescription. Always confirm the
        pest on the ground and follow the applicable product label and local
        agricultural guidance.
      </p>
    `;

    if (intervention.intervention_appropriate) {
      simButtonHtml = `
        <button
          class="btn btn-primary btn-sm"
          id="openIntervSimBtn">
          🧪 Intervention Simulator
        </button>
      `;
    }
  } else {
    currentInterventionMatches = [];
  }

  interventionContentEl.innerHTML = `
    ${statusRowHtml}
    ${issueHtml}
    ${zonesHtml}
    ${reasonsHtml}
    ${warningsHtml}
    ${matchesHtml}
    ${confidenceNoteHtml}
    ${historyHtml}

    ${
      simButtonHtml
        ? `<div class="fi-actions-row">
            ${simButtonHtml}
          </div>`
        : ""
    }
  `;

  if (simButtonHtml) {
    document
      .getElementById("openIntervSimBtn")
      .addEventListener("click", openInterventionSimModal);
  }
}

// ------------------------------------------------------------ loading ---
async function loadIntervention() {
  if (!interventionContentEl) return;
  interventionContentEl.innerHTML = `<div class="empty-state">Loading intervention guidance…</div>`;

  try {
    const [ctxRes, optRes] = await Promise.all([
      fetch(`/api/fields/${FIELD_ID}/intervention/context`),
      fetch(`/api/fields/${FIELD_ID}/intervention/options`),
    ]);

    if (!ctxRes.ok) throw new Error("Could not load intervention context.");
    const ctxData = await ctxRes.json();

    if (!optRes.ok) {
      const errData = await optRes.json().catch(() => ({}));
      throw new Error(errData.error || "Could not compute intervention options.");
    }
    const optData = await optRes.json();

    currentInterventionContext = ctxData.context;
    currentIntervention = optData.intervention;
    renderIntervention(currentInterventionContext, currentIntervention);
  } catch (err) {
    interventionContentEl.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  }
}

// Exposed so diagnosis.js can refresh this panel right after a diagnosis
// completes (Field -> Analysis -> Risk -> Diagnosis -> Intervention).
window.refreshIntervention = loadIntervention;

// ------------------------------------------------------------ simulator ---
function renderSimForm() {
  const options = currentInterventionMatches
    .map((m) => `<option value="${m.id}">${escapeHtml(m.insecticide)} — ${escapeHtml(m.crop)} / ${escapeHtml(m.pest)}</option>`)
    .join("");

  const defaultArea = currentIntervention && currentIntervention.affected_area_ha != null
    ? currentIntervention.affected_area_ha
    : "";

  intervSimModalBody.innerHTML = `
    <p class="diag-caveat">This is a transparent dosage × area calculator using the approved-use reference range -- it is a planning tool, not a guaranteed dose or an AI-determined treatment.</p>
    <div class="interv-sim-row">
      <span class="interv-sim-label">Pesticide option</span>
      <select id="intervSimPesticideSelect" class="form-select form-select-sm">${options}</select>
    </div>
    <div class="interv-sim-row">
      <span class="interv-sim-label">Treatment area (ha)</span>
      <input type="number" min="0.01" step="0.01" id="intervSimAreaInput" class="form-control form-control-sm" value="${defaultArea}" placeholder="e.g. 0.5" />
    </div>
    <div class="auth-error" id="intervSimError"></div>
    <div class="diag-step-actions">
      <button class="btn btn-primary btn-sm" id="intervSimCalcBtn">Calculate</button>
    </div>
  `;

  document.getElementById("intervSimCalcBtn").addEventListener("click", runSimulation);
}

function openInterventionSimModal() {
  renderSimForm();
  getBsIntervSimModal().show();
}

function fmtRange(min, max) {
  if (min == null && max == null) return null;
  if (min === max) return `${min}`;
  return `${min} – ${max}`;
}

function renderSimResult(sim) {
  const p = sim.pesticide_use;
  const planning = sim.planning || {};

  const formulationLine = planning.formulation_min != null
    ? `${fmtRange(planning.formulation_min, planning.formulation_max)}${sim.formulation_reference_per_ha ? ` <span class="diag-caveat">(ref: ${escapeHtml(sim.formulation_reference_per_ha)} per ha)</span>` : ""}`
    : escapeHtml(sim.formulation_note || "Not available for this record.");

  const sprayLine = planning.spray_fluid_min != null
    ? `${fmtRange(planning.spray_fluid_min, planning.spray_fluid_max)} L${sim.spray_fluid_reference_per_ha ? ` <span class="diag-caveat">(ref: ${escapeHtml(sim.spray_fluid_reference_per_ha)} L per ha)</span>` : ""}`
    : escapeHtml(sim.spray_fluid_note || "Not available for this record.");

  intervSimModalBody.innerHTML = `
    <div class="interv-sim-summary">
      <strong>${escapeHtml(p.insecticide)}</strong> — ${escapeHtml(p.crop)} / ${escapeHtml(p.pest)}
    </div>

    <div class="interv-sim-row">
      <span class="interv-sim-label">Treatment area</span>
      <span>${escapeHtml(String(sim.area_ha))} ha</span>
    </div>

    <div class="interv-sim-row">
      <span class="interv-sim-label">Reference dosage (a.i./ha)</span>
      <span>${escapeHtml(p.dosage_ai_gm_ha || "—")}</span>
    </div>

    <div class="interv-sim-row">
      <span class="interv-sim-label">Estimated formulation needed</span>
      <span>${formulationLine}</span>
    </div>

    <div class="interv-sim-row">
      <span class="interv-sim-label">Estimated spray fluid</span>
      <span>${sprayLine}</span>
    </div>

    <p class="diag-caveat">
      ${escapeHtml(sim.disclaimer || "Planning/reference information only.")}
    </p>

    <p class="result-source">
      Source: ${escapeHtml(p.source || "—")}
    </p>

    <div class="diag-step-actions">
      <button class="btn btn-outline-soft btn-sm" id="intervSimBackBtn">← Back</button>
      <button class="btn btn-outline-soft btn-sm" id="intervSimCloseBtn">Close</button>
    </div>
  `;

  document
    .getElementById("intervSimBackBtn")
    .addEventListener("click", renderSimForm);

  document
    .getElementById("intervSimCloseBtn")
    .addEventListener("click", () => getBsIntervSimModal().hide());
}

async function runSimulation() {
  const errorEl = document.getElementById("intervSimError");
  errorEl.classList.remove("show");

  const select = document.getElementById("intervSimPesticideSelect");
  const areaInput = document.getElementById("intervSimAreaInput");
  const pesticideUseId = select ? select.value : null;
  const area = areaInput ? parseFloat(areaInput.value) : NaN;

  if (!pesticideUseId) {
    errorEl.textContent = "Select a pesticide option.";
    errorEl.classList.add("show");
    return;
  }
  if (!area || area <= 0) {
    errorEl.textContent = "Enter a treatment area greater than 0.";
    errorEl.classList.add("show");
    return;
  }

  const btn = document.getElementById("intervSimCalcBtn");
  btn.disabled = true;
  btn.textContent = "Calculating…";

  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/intervention/simulate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pesticide_use_id: parseInt(pesticideUseId, 10),
        affected_area_ha: area,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Simulation failed.");
    renderSimResult(data.simulation);
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add("show");
  } finally {
    btn.disabled = false;
    btn.textContent = "Calculate";
  }
}

// Initial load -- runs once when the field page loads, same as
// loadDiagnosisHistory() at the bottom of diagnosis.js.
loadIntervention();