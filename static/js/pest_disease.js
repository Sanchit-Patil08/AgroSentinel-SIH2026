
  const form = document.getElementById("detectForm");
  const button = document.getElementById("detectBtn");
  const errorBox = document.getElementById("errorBox");
  const emptyResult = document.getElementById("emptyResult");
  const loading = document.getElementById("loading");
  const resultEl = document.getElementById("result");
  const photoInput = document.getElementById("photo");
  const photoPreview = document.getElementById("photoPreview");
  const photoLoadingText = document.getElementById("photoLoadingText");

  const MAX_IMAGE_BYTES = 8 * 1024 * 1024;

  const escapeHtml = (value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");

  const arr = (value) => Array.isArray(value) ? value : [];

  function showError(message) {
    errorBox.textContent = message;
    errorBox.style.display = "block";
  }

  function clearError() {
    errorBox.textContent = "";
    errorBox.style.display = "none";
  }

  function listHtml(items, emptyText = "No specific points returned.") {
    const safe = arr(items).filter(Boolean);

    if (!safe.length) {
      return `<div class="text-secondary" style="font-size:13px;">${escapeHtml(emptyText)}</div>`;
    }

    return `
      <ul class="simple-list">
        ${safe.map(item => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    `;
  }

  function candidatesHtml(items) {
    const safe = arr(items);

    if (!safe.length) {
      return `<div class="text-secondary" style="font-size:13px;">
        Gemini could not confidently rank alternative causes.
      </div>`;
    }

    return safe.map(item => `
      <div class="candidate">
        <div class="candidate-top">
          <strong>${escapeHtml(item.name || "Possible cause")}</strong>
          <span class="likelihood">${escapeHtml(item.likelihood || "")}</span>
        </div>
        <p>${escapeHtml(item.reason || "")}</p>
      </div>
    `).join("");
  }

  function renderResult(data, photoUsed) {
    const r = data || {};
    const confidence = r.confidence || "unknown";
    const confidenceNumber = Number(r.confidence_percent);
    const confidenceText = Number.isFinite(confidenceNumber)
      ? ` · ${confidenceNumber}%`
      : "";

    resultEl.innerHTML = `
      <div class="result-head">
        <div>
          <div class="problem">
            ${escapeHtml(r.likely_problem || "No clear diagnosis")}
          </div>
          <div class="confidence">
            ${escapeHtml(r.problem_type || "unknown")}
            · ${escapeHtml(confidence)} confidence${confidenceText}
          </div>
        </div>

        <div class="result-source">
          ${photoUsed ? "PHOTO + OBSERVATIONS" : "OBSERVATIONS"}
        </div>
      </div>

      <div class="result-section">
        <h3>What it may be</h3>
        ${candidatesHtml(r.what_it_may_be)}
      </div>

      <div class="result-section">
        <h3>What AgroSentinel noticed</h3>
        ${listHtml(r.what_you_are_seeing)}
      </div>

      <div class="result-section">
        <h3>Signs to check in the field</h3>
        ${listHtml(r.signs_to_check)}
      </div>

      <div class="result-section">
        <h3>What to do now</h3>
        ${listHtml(r.what_to_do_now)}
      </div>

      <div class="result-section">
        <h3>Avoid for now</h3>
        ${listHtml(r.avoid_for_now)}
      </div>

      <div class="result-section">
        <h3>When to get local help</h3>
        ${listHtml(r.when_to_get_help)}
      </div>

      <div class="notice">
        <strong>Important:</strong>
        ${escapeHtml(
          r.note ||
          "This is an AI-assisted indication, not a confirmed diagnosis."
        )}
        Do not spray a pesticide solely because of this result.
        Use the separate Pesticide Advisor for treatment information.
      </div>
    `;

    emptyResult.style.display = "none";
    loading.style.display = "none";
    resultEl.style.display = "block";
  }

  photoInput.addEventListener("change", () => {
    clearError();

    const file = photoInput.files && photoInput.files[0];

    if (!file) {
      photoPreview.style.display = "none";
      photoPreview.removeAttribute("src");
      return;
    }

    const allowed = ["image/jpeg", "image/png", "image/webp"];

    if (!allowed.includes(file.type)) {
      showError("Photo must be JPG, PNG, or WEBP.");
      photoInput.value = "";
      photoPreview.style.display = "none";
      return;
    }

    if (file.size > MAX_IMAGE_BYTES) {
      showError("That photo is larger than 8 MB. Please choose a smaller photo.");
      photoInput.value = "";
      photoPreview.style.display = "none";
      return;
    }

    const url = URL.createObjectURL(file);
    photoPreview.src = url;
    photoPreview.style.display = "block";
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearError();

    const crop = document.getElementById("crop").value;
    const appearance = document.getElementById("appearance").value.trim();
    const photo = photoInput.files && photoInput.files[0];

    if (!crop) {
      showError("Please choose the crop first.");
      return;
    }

    if (!appearance) {
      showError("Please describe what you are seeing on the plant.");
      return;
    }

    if (photo && photo.size > MAX_IMAGE_BYTES) {
      showError("Photo must be 8 MB or smaller.");
      return;
    }

    const formData = new FormData(form);

    button.disabled = true;
    button.textContent = "Checking…";

    emptyResult.style.display = "none";
    resultEl.style.display = "none";
    loading.style.display = "flex";
    photoLoadingText.textContent = photo ? " and the photo" : "";

    try {
      const response = await fetch("/api/pest-disease/detect", {
        method: "POST",
        body: formData,
        credentials: "same-origin",
      });

      const payload = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          payload.error || "Could not complete the crop check."
        );
      }

      renderResult(payload.result, payload.photo_used);
    } catch (error) {
      loading.style.display = "none";
      emptyResult.style.display = "grid";

      showError(
        error.message ||
        "Something went wrong while checking the crop. Please try again."
      );
    } finally {
      button.disabled = false;
      button.textContent = "🔎 Check My Crop";
    }
  });
