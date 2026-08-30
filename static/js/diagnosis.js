// "Diagnose This Field" modal flow (Stage 2 of the field-intelligence
// page). Reuses escapeHtml() from zone_render.js and FIELD_ID from the
// inline script in field_detail.html. Deliberately self-contained (its
// own small formatDateTime) so load order relative to field_detail.js
// doesn't matter.

const diagModalEl = document.getElementById("diagnoseModal");
const diagModalBody = document.getElementById("diagModalBody");
const diagnoseFieldBtn = document.getElementById("diagnoseFieldBtn");
const diagnosisHistoryEl = document.getElementById("diagnosisHistory");

let bsDiagModal = null;
let diagContext = null;
let diagCurrent = null; // the in-progress FieldDiagnosis record
let diagPendingEvidence = []; // evidence.to_dict() results added so far this session

function diagFormatDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch (e) {
    return iso;
  }
}

const DAMAGE_PATTERN_OPTIONS = [
  { value: "", label: "Not sure / skip" },
  { value: "chewing", label: "Chewing damage (holes, ragged edges)" },
  { value: "sucking", label: "Sucking damage (speckling, yellowing)" },
  { value: "curling", label: "Leaf curling" },
  { value: "wilting", label: "Wilting" },
  { value: "leaf_spot", label: "Leaf spots" },
  { value: "discoloration", label: "Discoloration" },
];

const IMAGE_TYPE_OPTIONS = [
  { value: "leaf", label: "Crop / leaf photo" },
  { value: "pest_insect", label: "Pest / insect photo" },
  { value: "closeup", label: "Close-up of affected area" },
  { value: "beneficial_insect", label: "Possible beneficial insect" },
  { value: "other", label: "Other" },
];

function getBsModal() {
  if (!bsDiagModal) {
    bsDiagModal = new bootstrap.Modal(diagModalEl);
  }
  return bsDiagModal;
}

// -------------------------------------------------------------- step A ---
function renderContextStep() {
  const ctx = diagContext;
  const f = ctx.field;
  const risk = ctx.risk;
  const zoneSummary = ctx.zone_summary || {};

  const zonesHtml = (ctx.priority_zones && ctx.priority_zones.length)
    ? ctx.priority_zones.map((z) => `<span class="diag-zone-chip ${escapeHtml(z.health_status)}">Zone ${z.zone_id}</span>`).join("")
    : `<span class="empty-state">No priority zones — field looks healthy or hasn't been analyzed yet.</span>`;

  const causesHtml = (risk && risk.causes && risk.causes.length)
    ? `<ul class="diag-cause-list">${risk.causes.slice(0, 3).map((c) => `<li>${escapeHtml(c.detail)}</li>`).join("")}</ul>`
    : `<p class="empty-state">No specific stress signals from satellite/weather/IoT yet.</p>`;

  const whyLine = zoneSummary.total
    ? `${zoneSummary.stressed}/${zoneSummary.total} zones show stress.`
    : `This field hasn't been analyzed yet — you can still start a manual inspection.`;

  diagModalBody.innerHTML = `
    <div class="diag-context">
      <h6 class="diag-field-title">${escapeHtml(f.name)} · ${escapeHtml(f.crop_type)} · ${escapeHtml(f.crop_stage)}</h6>
      <p class="diag-why">${escapeHtml(whyLine)}</p>

      ${ctx.priority_zones && ctx.priority_zones.length ? `<div class="diag-section-label">Priority zones</div><div class="diag-zone-chips">${zonesHtml}</div>` : ""}

      <div class="diag-section-label">Why are we asking for inspection?</div>
      ${causesHtml}
      <p class="diag-caveat">Satellite data can detect vegetation stress, but cannot reliably distinguish between pest, disease, nutrient deficiency and environmental stress. Ground evidence helps narrow it down.</p>
    </div>

    <div class="diag-section-label">How do you want to inspect?</div>
    <div class="diag-method-row">
      <button class="btn diag-method-btn" id="diagInspectYourselfBtn">
        <span class="diag-method-icon">👨‍🌾</span>
        <span class="diag-method-title">Inspect Yourself</span>
        <span class="diag-method-sub">Upload photos of the affected zones</span>
      </button>
      <button class="btn diag-method-btn disabled" title="Coming soon">
        <span class="diag-method-icon">🚁</span>
        <span class="diag-method-title">Request Drone Inspection</span>
        <span class="diag-method-sub">Coming soon</span>
      </button>
    </div>
  `;

  document.getElementById("diagInspectYourselfBtn").addEventListener("click", startManualInspection);
}

async function openDiagnoseModal() {
  diagModalBody.innerHTML = `<div class="empty-state">Loading field context…</div>`;
  getBsModal().show();
  diagCurrent = null;
  diagPendingEvidence = [];

  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/diagnosis/context`);
    if (!res.ok) throw new Error("Could not load field context.");
    const data = await res.json();
    diagContext = data.context;
    renderContextStep();
  } catch (err) {
    diagModalBody.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  }
}

if (diagnoseFieldBtn) {
  diagnoseFieldBtn.addEventListener("click", openDiagnoseModal);
}

// -------------------------------------------------------------- step B ---
async function startManualInspection() {
  diagModalBody.innerHTML = `<div class="empty-state">Starting inspection…</div>`;
  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/diagnosis`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ inspection_method: "manual" }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Could not start diagnosis.");
    diagCurrent = data.diagnosis;
    diagPendingEvidence = [];
    renderEvidenceStep();
  } catch (err) {
    diagModalBody.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  }
}

// function evidenceThumbHtml(item) {
//   const typeLabel = (IMAGE_TYPE_OPTIONS.find((o) => o.value === item.image_type) || {}).label || item.image_type;
//   return `
//     <div class="diag-evidence-item">
//       <img src="${item.url}" alt="${escapeHtml(item.original_filename || "evidence")}" />
//       <div class="diag-evidence-meta">
//         <span>${escapeHtml(typeLabel)}</span>
//         ${item.damage_pattern ? `<span class="diag-evidence-tag">${escapeHtml(item.damage_pattern.replace("_", " "))}</span>` : ""}
//       </div>
//     </div>`;
// }

function evidenceThumbHtml(item) {
  const typeLabel =
    (IMAGE_TYPE_OPTIONS.find((o) => o.value === item.image_type) || {}).label
    || item.image_type;

  const zoneLabel = item.zone_id != null
    ? `Zone ${item.zone_id}`
    : "Field";

  return `
    <div class="diag-evidence-item">
      <img
        src="${item.url}"
        alt="${escapeHtml(item.original_filename || "evidence")}"
      />

      <div class="diag-evidence-meta">
        <span>${escapeHtml(zoneLabel)}</span>
        <span>${escapeHtml(typeLabel)}</span>

        ${item.damage_pattern
          ? `<span class="diag-evidence-tag">
              ${escapeHtml(item.damage_pattern.replace("_", " "))}
            </span>`
          : ""}
      </div>
    </div>
  `;
}

function renderEvidenceStep() {
  const zonesHtml = (diagCurrent.priority_zones && diagCurrent.priority_zones.length)
    ? diagCurrent.priority_zones.map((z) => `<span class="diag-zone-chip ${escapeHtml(z.health_status)}">Zone ${z.zone_id}</span>`).join("")
    : "";

  const imageTypeOptionsHtml = IMAGE_TYPE_OPTIONS.map((o) => `<option value="${o.value}">${escapeHtml(o.label)}</option>`).join("");
  const damagePatternOptionsHtml = DAMAGE_PATTERN_OPTIONS.map((o) => `<option value="${o.value}">${escapeHtml(o.label)}</option>`).join("");

  const priorityZoneOptionsHtml =
    (diagCurrent.priority_zones && diagCurrent.priority_zones.length)
      ? diagCurrent.priority_zones
        .map((z) => `
          <option value="${escapeHtml(String(z.zone_id))}">
            Zone ${escapeHtml(String(z.zone_id))} — ${escapeHtml(z.health_status)}
          </option>
        `)
        .join("")
      : `<option value="">Entire field / no specific zone</option>`;

  diagModalBody.innerHTML = `
    <div class="diag-context">
      <h6 class="diag-field-title">Inspect ${zonesHtml ? "these zones" : "the field"}</h6>
      ${zonesHtml ? `<div class="diag-zone-chips">${zonesHtml}</div>` : ""}
      <p class="diag-caveat">Upload a crop/leaf photo, a pest/insect photo, or a close-up of the affected area. You can add more than one, or continue without any.</p>
    </div>

    <div id="diagEvidenceList" class="diag-evidence-grid">${diagPendingEvidence.map(evidenceThumbHtml).join("")}</div>

    <form id="diagUploadForm" class="diag-upload-form">
      <div class="diag-upload-row">
  <input type="file" id="diagFileInput" accept="image/*" required />

  <select id="diagZoneId">
    ${priorityZoneOptionsHtml}
  </select>

  <select id="diagImageType">
    ${imageTypeOptionsHtml}
  </select>

  <select id="diagDamagePattern">
    ${damagePatternOptionsHtml}
  </select>
</div>
      <input type="text" id="diagEvidenceNote" class="form-control form-control-sm" placeholder="Optional note (e.g. 'holes on lower leaves, north corner')" />
      <div class="diag-upload-actions">
        <button type="submit" class="btn btn-outline-soft btn-sm" id="diagAddPhotoBtn">Add Photo</button>
      </div>
    </form>
    <div class="auth-error" id="diagUploadError"></div>

    <textarea id="diagFarmerNotes" class="form-control form-control-sm diag-notes" rows="2" placeholder="Optional: describe what you're seeing in your own words"></textarea>

    <div class="diag-step-actions">
      <button class="btn btn-outline-soft btn-sm" id="diagBackBtn">← Back</button>
      <button class="btn btn-primary btn-sm" id="diagRunBtn">Get Diagnosis</button>
    </div>
  `;

  document.getElementById("diagUploadForm").addEventListener("submit", uploadEvidence);
  document.getElementById("diagBackBtn").addEventListener("click", renderContextStep);
  document.getElementById("diagRunBtn").addEventListener("click", submitDiagnosis);
}

async function uploadEvidence(e) {
  e.preventDefault();
  const fileInput = document.getElementById("diagFileInput");
  const errorEl = document.getElementById("diagUploadError");
  errorEl.classList.remove("show");

  const file = fileInput.files[0];
  if (!file) return;

  const addBtn = document.getElementById("diagAddPhotoBtn");
  addBtn.disabled = true;
  addBtn.textContent = "Uploading…";

  const formData = new FormData();
  formData.append("image", file);
  formData.append("image_type", document.getElementById("diagImageType").value);
  const analysisId = diagCurrent.analysis_id;

  if (analysisId) {
    formData.append("analysis_id", analysisId);
  }

  const zoneId = document.getElementById("diagZoneId").value;

  if (zoneId) {
    formData.append("zone_id", zoneId);
  }
  const damagePattern = document.getElementById("diagDamagePattern").value;
  if (damagePattern) formData.append("damage_pattern", damagePattern);
  const note = document.getElementById("diagEvidenceNote").value.trim();
  if (note) formData.append("note", note);

  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/diagnosis/${diagCurrent.id}/evidence`, {
      method: "POST",
      body: formData,
    });
    // const data = await res.json();
    // if (!res.ok) throw new Error(data.error || "Could not upload photo.");
    const text = await res.text();

if (!res.ok) {
  console.error("Server response:", text);
  throw new Error(`Upload failed (${res.status}): ${text}`);
}

const data = JSON.parse(text);
    diagPendingEvidence.push(data.evidence);
    document.getElementById("diagEvidenceList").innerHTML = diagPendingEvidence.map(evidenceThumbHtml).join("");
    fileInput.value = "";
    document.getElementById("diagEvidenceNote").value = "";
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.add("show");
  } finally {
    addBtn.disabled = false;
    addBtn.textContent = "Add Photo";
  }
}

async function submitDiagnosis() {
  const runBtn = document.getElementById("diagRunBtn");
  runBtn.disabled = true;
  runBtn.textContent = "Diagnosing…";

  const farmerNotes = document.getElementById("diagFarmerNotes").value.trim();

  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/diagnosis/${diagCurrent.id}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ farmer_notes: farmerNotes }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Diagnosis failed.");
    diagCurrent = data.diagnosis;
    renderResultStep();
    loadDiagnosisHistory();
    // Field -> Analysis -> Risk -> Diagnosis -> Intervention: let the
    // Intervention panel (intervention.js) re-evaluate now that a new
    // diagnosis exists. Guarded since intervention.js may not be loaded
    // on every page that includes this file.
    if (typeof window.refreshIntervention === "function") {
      window.refreshIntervention();
    }
  } catch (err) {
    diagModalBody.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  } finally {
    runBtn.disabled = false;
    runBtn.textContent = "Get Diagnosis";
  }
}

// -------------------------------------------------------------- step C ---
function confidencePillHtml(level) {
  const l = (level || "low").toLowerCase();
  return `<span class="diag-confidence-pill ${escapeHtml(l)}">${escapeHtml(l.charAt(0).toUpperCase() + l.slice(1))} confidence</span>`;
}

function renderResultStep() {
  const d = diagCurrent;
  const evidenceHtml = (d.supporting_evidence || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("");
  const verifyHtml = (d.recommended_verification || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("");
  const interventionHtml = (d.recommended_intervention || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("");

  diagModalBody.innerHTML = `
    <div class="diag-result">
      <div class="diag-result-header">
        <span class="diag-result-cause">${escapeHtml(d.possible_cause || "—")}</span>
        ${confidencePillHtml(d.confidence_level)}
      </div>

      ${d.protection_alert ? `<div class="diag-protection-alert">🐞 ${escapeHtml(d.protection_alert)}</div>` : ""}

      ${evidenceHtml ? `<div class="diag-section-label">Supporting evidence</div><ul class="diag-list">${evidenceHtml}</ul>` : ""}
      ${verifyHtml ? `<div class="diag-section-label">Recommended verification</div><ul class="diag-list">${verifyHtml}</ul>` : ""}
      ${interventionHtml ? `<div class="diag-section-label">Recommended next step</div><ul class="diag-list">${interventionHtml}</ul>` : ""}

      <div class="result-source">Diagnosed ${escapeHtml(diagFormatDateTime(d.updated_at))} · manual inspection</div>
    </div>
    <div class="diag-step-actions">
      <button class="btn btn-outline-soft btn-sm" id="diagCloseBtn">Close</button>
    </div>
  `;

  document.getElementById("diagCloseBtn").addEventListener("click", () => {
    getBsModal().hide();
  });
}

// ------------------------------------------------------------- history ---
function diagConditionClass(level) {
  return { low: "healthy", medium: "moderate", high: "stressed" }[(level || "").toLowerCase()] || "unknown";
}

async function loadDiagnosisHistory() {
  if (!diagnosisHistoryEl) return;
  try {
    const res = await fetch(`/api/fields/${FIELD_ID}/diagnosis`);
    if (!res.ok) throw new Error("Could not load diagnosis history.");
    const data = await res.json();
    if (!data.diagnoses || !data.diagnoses.length) {
      diagnosisHistoryEl.innerHTML = `<div class="empty-state">No diagnoses yet — click "Diagnose This Field" above to start one.</div>`;
      return;
    }
    diagnosisHistoryEl.innerHTML = data.diagnoses
      .map((d) => {
        const methodLabel = d.inspection_method === "drone" ? "🚁 Drone inspection" : "👨‍🌾 Manual inspection";
        const statusLabel = d.status === "diagnosed" ? (d.possible_cause || "Diagnosed") : "Awaiting evidence";
        return `
        <div class="history-row diag-history-row">
          <div>
            <span class="condition-pill ${diagConditionClass(d.confidence_level)}" style="font-size:0.7rem;padding:3px 10px;">${escapeHtml(d.confidence_level || "—")}</span>
            <span class="h-meta">&nbsp;${escapeHtml(methodLabel)} · ${escapeHtml(statusLabel)} · ${d.evidence_count ?? 0} photo(s)</span>
          </div>
          <span class="h-date">${escapeHtml(diagFormatDateTime(d.created_at))}</span>
        </div>`;
      })
      .join("");
  } catch (err) {
    diagnosisHistoryEl.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  }
}

loadDiagnosisHistory();