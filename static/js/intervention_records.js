// intervention_records.js
// ================================================================
// Stage 4 action screen: RECORD INTERVENTION.
// Reached only after the farmer already chose a pathway via
// "Select This Option" in the option-details modal (intervention.js).
// The pathway is already known -- this modal collects only what's
// needed to record it, never re-asks the decision.
// ================================================================

const recordModalEl = document.getElementById("recordInterventionModal");
const recordModalBody = document.getElementById("recordInterventionModalBody");
let bsRecordModal = null;
function getBsRecordModal() {
  if (!bsRecordModal) bsRecordModal = new bootstrap.Modal(recordModalEl);
  return bsRecordModal;
}

let recordSelection = null; // { selectedOption, pesticideUseId, area }

const OPTION_TITLES = {
  targeted: "🎯 Treat Affected Zones",
  field_wide: "🌾 Treat Entire Field",
  monitor: "👁 Monitor For Now",
};

window.openRecordInterventionModal = function openRecordInterventionModal(selection) {
  if (!currentIntervention) {
    recordModalBody.innerHTML = `<div class="empty-state">Load the intervention panel first.</div>`;
    getBsRecordModal().show();
    return;
  }

  recordSelection = selection;
  const { selectedOption, pesticideUseId, area } = selection;
  const isTreatment = selectedOption === "targeted" || selectedOption === "field_wide";
  const matches = currentIntervention.matches || [];
  const chosenMatch = matches.find((m) => String(m.id) === String(pesticideUseId));

  const productBlock = isTreatment ? `
    <div class="interv-sim-row">
      <span class="interv-sim-label">Approved-use product</span>
      <div class="interv-detail-card">
        <strong>${escapeHtml(chosenMatch ? chosenMatch.insecticide : "—")}</strong>
        <div>${escapeHtml(chosenMatch ? `${chosenMatch.crop} / ${chosenMatch.pest}` : "")}</div>
      </div>
    </div>
    <div class="interv-sim-row">
      <span class="interv-sim-label">Treatment area (ha)</span>
      <input
  type="text"
  id="recAreaInput"
  class="form-control form-control-sm"
  value="${area != null ? Number(area).toFixed(3) + " ha" : "—"}"
  readonly
/>
    </div>
    <div class="interv-sim-row">
      <span class="interv-sim-label">Local price / unit (₹, optional)</span>
      <input type="number" min="0" step="0.01" id="recPriceInput" class="form-control form-control-sm" placeholder="e.g. 650" />
    </div>
    <p class="diag-caveat">Entering a local price stores an approximate planning cost with this record. Leave blank to record quantity only.</p>
  ` : `
    <p class="diag-caveat">👁 You are recording a monitoring decision. No pesticide treatment will be recorded. Estimated cost: ₹0.</p>
  `;

  recordModalBody.innerHTML = `
    <p class="diag-caveat">You selected: <strong>${escapeHtml(OPTION_TITLES[selectedOption] || selectedOption)}</strong></p>
    ${productBlock}
    <div class="interv-sim-row" style="margin-top:12px;">
      <span class="interv-sim-label">Notes (optional)</span>
      <textarea id="recNotesInput" class="form-control form-control-sm" rows="2" placeholder="Anything worth remembering about this decision"></textarea>
    </div>
    <div class="auth-error" id="recError"></div>
    <div class="diag-step-actions">
      <button class="btn btn-primary btn-sm" id="recSubmitBtn">Record Decision</button>
    </div>
  `;

  document.getElementById("recSubmitBtn").addEventListener("click", submitRecordIntervention);
  getBsRecordModal().show();
};

async function submitRecordIntervention() {
  const errorEl = document.getElementById("recError");
  errorEl.classList.remove("show");

  const { selectedOption, pesticideUseId } = recordSelection || {};
  const body = {
    selected_option: selectedOption,
    farmer_notes: document.getElementById("recNotesInput").value,
  };

  if (selectedOption === "targeted" || selectedOption === "field_wide") {
    if (!pesticideUseId) {
      errorEl.textContent = "No approved-use option was selected.";
      errorEl.classList.add("show");
      return;
    }
    body.pesticide_use_id = pesticideUseId;

    const area = recordSelection && recordSelection.area != null
      ? Number(recordSelection.area)
      : null;
    if (selectedOption === "targeted") body.affected_area_ha = area;

    const priceInput = document.getElementById("recPriceInput");
    if (priceInput && priceInput.value) {
      const price = parseFloat(priceInput.value);
      if (Number.isNaN(price) || price < 0) {
        errorEl.textContent = "Enter a valid local price.";
        errorEl.classList.add("show");
        return;
      }
      body.price_per_unit_inr = price;
    }
  }

  const btn = document.getElementById("recSubmitBtn");
  btn.disabled = true;
  btn.textContent = "Recording…";

  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/intervention/record`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Could not record intervention.");

    getBsRecordModal().hide();
    switchTab("interventions");
    if (typeof window.loadInterventionHistory === "function") window.loadInterventionHistory();
    if (typeof window.refreshIntervention === "function") window.refreshIntervention();
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add("show");
  } finally {
    btn.disabled = false;
    btn.textContent = "Record Decision";
  }
}