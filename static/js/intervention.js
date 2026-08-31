// intervention.js
// ================================================================
// Renders the "What can you do?" three-pathway decision panel and
// the per-option "View Details" modal. This file NEVER decides
// MONITOR / TARGETED / FIELD_WIDE -- that decision comes from the
// backend's recommend_interventions() (Intervention Engine). It only
// presents the three possible pathways and, when the farmer opens
// one, that option's own focused detail view -- never all three at
// once, never a shared pesticide bucket.
//
// Selecting an option hands off to intervention_records.js via
// window.openRecordInterventionModal({selectedOption, pesticideUseId, area})
// -- the pathway is already known at that point and is never re-asked.
// ================================================================

const interventionContentEl = document.getElementById("interventionContent");
const optionDetailsModalEl = document.getElementById("optionDetailsModal");
const optionDetailsModalBody = document.getElementById("optionDetailsModalBody");
const optionDetailsModalTitle = document.getElementById("optionDetailsModalLabel");

let bsOptionDetailsModal = null;
function getBsOptionDetailsModal() {
  if (!bsOptionDetailsModal) bsOptionDetailsModal = new bootstrap.Modal(optionDetailsModalEl);
  return bsOptionDetailsModal;
}

let currentInterventionContext = null;
let currentIntervention = null;
let targetedAvailableFlag = false;
let fieldWideAvailableFlag = false;

const FOLLOWUP_WINDOW_TEXT = {
  targeted: "about 5–7 days",
  field_wide: "about 5–7 days",
  monitor: "about 7–10 days",
  
};
const ESTIMATED_PRICE_PER_100G = 250;

function fmtHa(v) {
  return v == null ? "—" : `${v} ha`;
}

// ---------------------------------------------------------------- panel --
function renderIntervention(context, intervention) {
  if (!interventionContentEl) return;

  if (!intervention) {
    interventionContentEl.innerHTML = `<div class="empty-state">Could not load intervention guidance.</div>`;
    return;
  }

  currentInterventionContext = context;
  currentIntervention = intervention;

  const diag = context && context.diagnosis;
  const zones = context && context.affected_zones;

  // No diagnosis yet -- don't render three cards with nothing behind
  // them (see recommend_interventions()'s "no diagnosis" early return).
  if (!diag || diag.status !== "diagnosed") {
    interventionContentEl.innerHTML = `
      <div class="empty-state">Run "Diagnose This Field" to get intervention guidance.</div>
    `;
    return;
  }

  const matches = intervention.matches || [];
  const targeting = intervention.targeting || {};

  targetedAvailableFlag = matches.length > 0;
  fieldWideAvailableFlag = !!targeting.field_wide_available;

  const firstMatch = matches[0];
const pricePer100g = 250;

function estimateCardCost(area) {
  if (!firstMatch || !area) return null;

  // Temporary prototype estimate
  const estimatedGramsPerHa = 1000;
  const quantityGrams = Number(area) * estimatedGramsPerHa;

  return Math.round(
    (quantityGrams / 100) * pricePer100g
  );
}

const targetedCost = estimateCardCost(
  intervention.affected_area_ha
);

const fieldWideCost = estimateCardCost(
  context.field && context.field.area_ha
);

  const situationHtml = `
    <div class="interv-situation">
      ${diag.possible_cause ? `<p class="interv-possible-issue"><strong>Diagnosis:</strong> ${escapeHtml(diag.possible_cause)}${diag.confidence_level ? ` — ${escapeHtml(diag.confidence_level)} confidence` : ""}</p>` : ""}
      ${context.risk && context.risk.risk_level ? `<p class="interv-possible-issue"><strong>Risk:</strong> ${escapeHtml(context.risk.risk_level)}</p>` : ""}
      ${zones && zones.total ? `<p class="interv-zones"><strong>Affected zones:</strong> ${zones.stressed} / ${zones.total}</p>` : ""}
      ${intervention.affected_area_ha != null ? `<p class="interv-zones"><strong>Affected area:</strong> ~${fmtHa(intervention.affected_area_ha)}</p>` : ""}
    </div>
  `;
const cardsHtml = [
  optionCard({
    key: "targeted",
    icon: "🎯",
    title: "Treat affected zones",
    tagline: "Treat only the affected area",
    recommended: intervention.recommended_pathway === "targeted",
    statusLabel: targetedAvailableFlag ? "Available" : "Not available",
    costLabel: targetedCost != null
      ? `Approx. cost: ₹${targetedCost.toLocaleString("en-IN")}`
      : "Cost unavailable",
  }),

  optionCard({
    key: "field_wide",
    icon: "🌾",
    title: "Treat entire field",
    tagline: "Apply treatment across the whole field",
    recommended: intervention.recommended_pathway === "field_wide",
    statusLabel: fieldWideAvailableFlag ? "Available" : "Not justified",
    costLabel: fieldWideCost != null
      ? `Approx. cost: ₹${fieldWideCost.toLocaleString("en-IN")}`
      : "Cost unavailable",
  }),

  optionCard({
    key: "monitor",
    icon: "👁",
    title: "Monitor for now",
    tagline: "No immediate treatment",
    recommended: intervention.recommended_pathway === "monitor",
    statusLabel: "Available",
    costLabel: "Approx. cost: ₹0",
  }),
].join("");
  
  interventionContentEl.innerHTML = `
    ${situationHtml}
    <div class="diag-section-label">What can you do?</div>
    <div class="interv-options-cards">${cardsHtml}</div>
  `;

  interventionContentEl.querySelectorAll("[data-option-details]").forEach((btn) => {
    btn.addEventListener("click", () => openOptionDetails(btn.getAttribute("data-option-details")));
  });
}

function optionCard({
  key,
  icon,
  title,
  tagline,
  recommended,
  statusLabel,
  costLabel
}) {
  return `
    <div class="interv-option-card ${recommended ? "interv-option-recommended" : ""}">
      
      <div class="interv-option-head">
        <span class="interv-option-icon">${icon}</span>
        <span class="interv-option-title">${escapeHtml(title)}</span>
      </div>

      <div class="interv-option-status ${recommended ? "is-recommended" : ""}">
        ${escapeHtml(statusLabel)}
      </div>

      <p class="interv-option-tagline">
        ${escapeHtml(tagline)}
      </p>

      ${costLabel ? `
        <div class="interv-option-cost">
          ${escapeHtml(costLabel)}
        </div>
      ` : ""}

      <button class="btn btn-outline-soft btn-sm"
              data-option-details="${key}">
        View Details
      </button>

    </div>
  `;
}

// ------------------------------------------------------------- details --
async function openOptionDetails(optionKey) {
  if (!currentIntervention) return;

  optionDetailsModalTitle.textContent = {
    targeted: "🎯 Treat Affected Zones",
    field_wide: "🌾 Treat Entire Field",
    monitor: "👁 Monitor For Now",
  }[optionKey] || "Option Details";

  optionDetailsModalBody.innerHTML = `<div class="empty-state">Loading…</div>`;
  getBsOptionDetailsModal().show();

  if (optionKey === "monitor") {
    optionDetailsModalBody.innerHTML = renderMonitorDetails();
    wireSelectButton("monitor");
    return;
  }
  // Commented out because it was just flagging only one option
  // if (optionKey === "field_wide" && !fieldWideAvailableFlag) {
  //   const reason = (currentIntervention.targeting || {}).field_wide_reason
  //     || "The current stressed-zone extent and risk level do not support treating the entire field. Treating only the affected zones is preferred.";
  //   optionDetailsModalBody.innerHTML = `
  //     <p class="interv-possible-issue">Field-wide treatment is not currently justified.</p>
  //     <p class="diag-caveat">${escapeHtml(reason)}</p>
  //     <div class="diag-step-actions"><button class="btn btn-outline-soft btn-sm" data-bs-dismiss="modal">Close</button></div>
  //   `;
  //   return;
  // }

const canSelect = true;
optionDetailsModalBody.innerHTML =
  await renderTreatmentDetails(optionKey, canSelect);

wireTreatmentDetailListeners(optionKey, canSelect);
}

function renderMonitorDetails() {
  const diag = currentInterventionContext && currentInterventionContext.diagnosis;
  const verify = (diag && diag.recommended_verification) || [];
  const reasons = currentIntervention.reasons || [];

  return `
    <div class="diag-section-label">Why monitoring is reasonable</div>
    <ul class="diag-list">
      ${(reasons.length ? reasons : ["No strong signal currently supports treatment."]).map((r) => `<li>${escapeHtml(r)}</li>`).join("")}
    </ul>
    ${verify.length ? `
      <div class="diag-section-label">What to watch for</div>
      <ul class="diag-list">${verify.map((v) => `<li>${escapeHtml(v)}</li>`).join("")}</ul>
    ` : ""}
    <div class="diag-section-label">When AgroSentinel will reassess</div>
    <p class="interv-zones">Suggested follow-up window: ${FOLLOWUP_WINDOW_TEXT.monitor}.</p>
    <div class="diag-section-label">Cost</div>
    <p class="interv-zones"><strong>₹0</strong> — no treatment is applied.</p>
    ${currentIntervention.confidence_note ? `<p class="interv-confidence-note">${escapeHtml(currentIntervention.confidence_note)}</p>` : ""}
    <div class="diag-step-actions">
      <button class="btn btn-primary btn-sm" id="optionSelectBtn">Select This Option</button>
      <button class="btn btn-outline-soft btn-sm" data-bs-dismiss="modal">Close</button>
    </div>
  `;
}
async function renderTreatmentDetails(optionKey, canSelect) {
  const matches = currentIntervention.matches || [];

  if (!matches.length) {
  const reasons = currentIntervention.reasons || [];
  const warnings = currentIntervention.warnings || [];

  return `
    <div class="interv-detail-content">

      <div class="diag-section-label">
        Current assessment
      </div>

      <p class="interv-possible-issue">
        Targeted pesticide treatment is not currently supported by
        the available field evidence.
      </p>

      ${
        reasons.length
          ? `
            <div class="diag-section-label">
              Why
            </div>
            <ul class="diag-list">
              ${reasons.map(r => `<li>${escapeHtml(r)}</li>`).join("")}
            </ul>
          `
          : ""
      }

      ${
        currentIntervention.confidence_note
          ? `
            <div class="diag-section-label">
              What should happen first
            </div>
            <p class="diag-caveat">
              ${escapeHtml(currentIntervention.confidence_note)}
            </p>
          `
          : ""
      }

      ${
        warnings.length
          ? `
            <div class="diag-section-label">
              Important
            </div>
            <ul class="diag-list">
              ${warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("")}
            </ul>
          `
          : ""
      }

      <div class="diag-step-actions">
        <button
          class="btn btn-outline-soft btn-sm"
          data-bs-dismiss="modal"
        >
          Close
        </button>
      </div>

    </div>
  `;
}

  const area = optionKey === "field_wide"
    ? (currentInterventionContext.field &&
       currentInterventionContext.field.area_ha)
    : currentIntervention.affected_area_ha;

  const selectedMatch = matches[0];

  const reasons = currentIntervention.reasons || [];
  const warnings = currentIntervention.warnings || [];
  const zones = currentInterventionContext.affected_zones;

  return `
    <div class="interv-detail-content">

      <div class="diag-section-label">
        Why this is recommended
      </div>

      <ul class="diag-list">
        ${
          reasons.length
            ? reasons.map(r => `<li>${escapeHtml(r)}</li>`).join("")
            : `<li>
                 The affected zones show signs consistent with the
                 current diagnosis.
               </li>`
        }
      </ul>

      <div class="interv-detail-card">

        <div class="interv-detail-row">
          <span>Zones affected</span>
          <strong>
            ${
              zones && zones.total
                ? `${zones.stressed} of ${zones.total}`
                : "—"
            }
          </strong>
        </div>

        <div class="interv-detail-row">
          <span>Area to treat</span>
          <strong>~${fmtHa(area)}</strong>
        </div>

      </div>

      <div class="diag-section-label">
        Recommended treatment
      </div>

      <div class="interv-product-card">

        <div class="interv-product-name">
          ${escapeHtml(selectedMatch.insecticide || "Treatment option")}
        </div>

        <div class="interv-product-subtitle">
          ${escapeHtml(selectedMatch.crop || "")}
          ${
            selectedMatch.pest
              ? ` · ${escapeHtml(selectedMatch.pest)}`
              : ""
          }
        </div>

      </div>

      <div class="diag-section-label">
        Estimated amount
      </div>

      <div id="optDetailQuantity">
        <div class="empty-state">
          Calculating…
        </div>
      </div>

      <div class="diag-section-label">
        When to check again
      </div>

      <p class="interv-zones">
        About ${FOLLOWUP_WINDOW_TEXT[optionKey]}.
      </p>

      ${
        warnings.length
          ? `
            <div class="diag-section-label">
              Important
            </div>

            <ul class="diag-list">
              ${warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("")}
            </ul>
          `
          : ""
      }

      <div class="diag-caveat">
        Confirm the pest on the ground before treatment.
        Always follow the product label and local agricultural guidance.
      </div>

      ${
        matches.length > 1
          ? `
            <details class="interv-alternatives">
              <summary>
                View other approved treatment options
                (${matches.length - 1})
              </summary>

              <div class="interv-alternative-list">

                ${matches.slice(1).map(m => `
                  <div class="interv-alternative-item">
                    <strong>
                      ${escapeHtml(m.insecticide || "Treatment option")}
                    </strong>
                    <span>
                      ${escapeHtml(m.crop || "")}
                      ${m.pest ? ` · ${escapeHtml(m.pest)}` : ""}
                    </span>
                  </div>
                `).join("")}

              </div>
            </details>
          `
          : ""
      }

      <div class="diag-step-actions">

        ${
          canSelect
            ? `
              <button
                class="btn btn-primary btn-sm"
                id="optionSelectBtn"
              >
                Select This Option
              </button>
            `
            : ""
        }

        <button
          class="btn btn-outline-soft btn-sm"
          data-bs-dismiss="modal"
        >
          Close
        </button>

      </div>

    </div>
  `;
}

function wireTreatmentDetailListeners(optionKey, canSelect) {
  refreshOptionDetailQuantity(optionKey);

  if (canSelect) {
    wireSelectButton(optionKey);
  }
}

async function refreshOptionDetailQuantity(optionKey) {
  const qtyEl = document.getElementById("optDetailQuantity");

  if (!qtyEl) return;

  const matches = currentIntervention.matches || [];

  if (!matches.length) {
    qtyEl.innerHTML = `
      <div class="empty-state">
        No treatment quantity available.
      </div>
    `;
    return;
  }

  /*
   * IMPORTANT:
   * The selected treatment is deliberately the first match for now.
   * We are NOT exposing the raw pesticide database as a dropdown.
   *
   * We still use the actual database ID returned by the backend.
   */
  const match = matches[0];

  const area = optionKey === "field_wide"
    ? (
        currentInterventionContext.field &&
        currentInterventionContext.field.area_ha
      )
    : currentIntervention.affected_area_ha;

  if (!match.id || !area) {
    qtyEl.innerHTML = `
      <div class="empty-state">
        Treatment quantity could not be calculated.
      </div>
    `;
    return;
  }

  qtyEl.innerHTML = `
    <div class="empty-state">
      Calculating estimated amount…
    </div>
  `;

  try {
    const res = await fetch(
      `/api/fields/${FIELD_ID}/intervention/simulate`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          pesticide_use_id: Number(match.id),
          affected_area_ha: Number(area)
        })
      }
    );

    const data = await res.json();

    if (!res.ok) {
      throw new Error(
        data.error || "Could not calculate treatment quantity."
      );
    }

    const planning =
      data.simulation &&
      data.simulation.planning
        ? data.simulation.planning
        : {};

    let html = "";

let costHtml = "";

if (
  planning.formulation_min != null &&
  planning.formulation_max != null
) {
  const costMin =
    (Number(planning.formulation_min) / 100) *
    ESTIMATED_PRICE_PER_100G;

  const costMax =
    (Number(planning.formulation_max) / 100) *
    ESTIMATED_PRICE_PER_100G;

  costHtml = `
    <div class="interv-detail-card">
      <div class="interv-detail-row">
        <span>Estimated treatment cost</span>
        <strong>
          ₹${Math.round(costMin).toLocaleString("en-IN")}
          –
          ₹${Math.round(costMax).toLocaleString("en-IN")}
        </strong>
      </div>
    </div>

    <p class="interv-confidence-note">
      Approximate planning estimate. Actual market price may vary.
    </p>
  `;
}

    if (
      planning.formulation_min != null &&
      planning.formulation_max != null
    ) {
      html += `
        <div class="interv-detail-card">

          <div class="interv-detail-row">
            <span>Amount of product</span>
            <strong>
              ~${escapeHtml(String(planning.formulation_min))}
              –
              ${escapeHtml(String(planning.formulation_max))}
              g
            </strong>
          </div>

          ${
            planning.spray_fluid_min != null &&
            planning.spray_fluid_max != null
              ? `
                <div class="interv-detail-row">
                  <span>Spray water</span>
                  <strong>
                    ~${escapeHtml(String(planning.spray_fluid_min))}
                    –
                    ${escapeHtml(String(planning.spray_fluid_max))}
                    L
                  </strong>
                </div>
              `
              : ""
          }

        </div>
      `;
    }

    if (!html) {
      html = `
        <div class="diag-caveat">
          ${escapeHtml(
            data.simulation.formulation_note ||
            "Quantity information is not available for this treatment option."
          )}
        </div>
      `;
    }

    qtyEl.innerHTML = html + costHtml;

  } catch (err) {

    qtyEl.innerHTML = `
      <div class="diag-caveat">
        ${escapeHtml(err.message)}
      </div>
    `;
  }
}

function wireSelectButton(optionKey) {
  const btn = document.getElementById("optionSelectBtn");
  if (!btn) return;

  btn.addEventListener("click", () => {
    const matches = currentIntervention.matches || [];
    const selectedMatch = matches[0];

    const pesticideUseId = selectedMatch && selectedMatch.id
      ? Number(selectedMatch.id)
      : null;

    const area = optionKey === "field_wide"
      ? (
          currentInterventionContext.field &&
          currentInterventionContext.field.area_ha
        )
      : currentIntervention.affected_area_ha;

    getBsOptionDetailsModal().hide();

    if (typeof window.openRecordInterventionModal === "function") {
      window.openRecordInterventionModal({
        selectedOption: optionKey,
        pesticideUseId,
        area
      });
    }
  });
}

// ================================================================
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

    renderIntervention(ctxData.context, optData.intervention);
  } catch (err) {
    interventionContentEl.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  }
}

window.refreshIntervention = loadIntervention;
loadIntervention();