const map = L.map("map").setView([19.10, 72.85], 13);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

let fieldPolygon = null;

const drawStatus = document.getElementById("drawStatus");
const saveBtn = document.getElementById("saveBtn");
const saveError = document.getElementById("saveError");
const fieldName = document.getElementById("fieldName");
const cropType = document.getElementById("cropType");
const cropStage = document.getElementById("cropStage");

const drawnItems = new L.FeatureGroup();
map.addLayer(drawnItems);

const drawControl = new L.Control.Draw({
  edit: { featureGroup: drawnItems },
  draw: {
    polygon: { allowIntersection: false, showArea: true },
    polyline: false,
    rectangle: false,
    circle: false,
    circlemarker: false,
    marker: false,
  },
});
map.addControl(drawControl);

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

map.on(L.Draw.Event.CREATED, (event) => {
  drawnItems.clearLayers();
  drawnItems.addLayer(event.layer);
  fieldPolygon = event.layer.getLatLngs()[0].map((p) => [p.lng, p.lat]);
  drawStatus.textContent = "Field boundary selected.";
  updateSaveButton();
});

map.on(L.Draw.Event.EDITED, () => {
  const layers = drawnItems.getLayers();
  if (!layers.length) {
    fieldPolygon = null;
    updateSaveButton();
    return;
  }
  fieldPolygon = layers[0].getLatLngs()[0].map((p) => [p.lng, p.lat]);
  drawStatus.textContent = "Field boundary updated.";
  updateSaveButton();
});

map.on(L.Draw.Event.DELETED, () => {
  fieldPolygon = null;
  drawStatus.textContent = "No field drawn yet — use the polygon tool on the map.";
  updateSaveButton();
});

function updateSaveButton() {
  saveBtn.disabled = !(
    fieldPolygon &&
    fieldPolygon.length >= 3 &&
    fieldName.value.trim() &&
    cropType.value &&
    cropStage.value
  );
}

[fieldName, cropType, cropStage].forEach((el) =>
  el.addEventListener("input", updateSaveButton)
);
cropType.addEventListener("change", updateSaveButton);
cropStage.addEventListener("change", updateSaveButton);

async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const config = await res.json();

    cropType.innerHTML = '<option value="">Select crop</option>';
    config.crop_types.forEach((t) => {
      const o = document.createElement("option");
      o.value = t;
      o.textContent = t;
      cropType.appendChild(o);
    });

    cropStage.innerHTML = '<option value="">Select crop stage</option>';
    config.crop_stages.forEach((s) => {
      const o = document.createElement("option");
      o.value = s;
      o.textContent = s;
      cropStage.appendChild(o);
    });
  } catch (err) {
    cropType.innerHTML = '<option value="">Failed to load</option>';
    cropStage.innerHTML = '<option value="">Failed to load</option>';
  }
  updateSaveButton();
}

saveBtn.addEventListener("click", saveField);

async function saveField() {
  saveError.classList.remove("show");
  saveBtn.disabled = true;
  saveBtn.textContent = "Saving…";

  try {
    const res = await fetch("/api/fields", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: fieldName.value.trim(),
        polygon: fieldPolygon,
        crop_type: cropType.value,
        crop_stage: cropStage.value,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to save field.");

    window.location.href = `/fields/${data.field.id}`;
  } catch (err) {
    saveError.textContent = err.message;
    saveError.classList.add("show");
    saveBtn.disabled = false;
    saveBtn.textContent = "Save Field";
  }
}

const searchInput = document.getElementById("searchInput");
const searchBtn = document.getElementById("searchBtn");
searchBtn.addEventListener("click", searchPlace);
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") searchPlace();
});

async function searchPlace() {
  const query = searchInput.value.trim();
  if (!query) return;
  searchBtn.disabled = true;
  searchBtn.textContent = "...";
  try {
    const res = await fetch(
      `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(query)}`
    );
    const places = await res.json();
    if (!places.length) {
      alert("Location not found.");
      return;
    }
    map.setView([parseFloat(places[0].lat), parseFloat(places[0].lon)], 15);
  } catch (err) {
    alert("Could not search for this location.");
  } finally {
    searchBtn.disabled = false;
    searchBtn.textContent = "Go";
  }
}

loadConfig();