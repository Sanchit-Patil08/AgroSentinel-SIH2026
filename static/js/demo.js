const map = L.map("map").setView([19.10, 72.85], 13);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);

let drawnField = null;
let fieldPolygon = null;
let zoneLayer = L.featureGroup().addTo(map);

const drawStatus = document.getElementById("drawStatus");
const analyzeBtn = document.getElementById("analyzeBtn");
const cropType = document.getElementById("cropType");
const cropStage = document.getElementById("cropStage");
const resultsArea = document.getElementById("resultsArea");
const loadingOverlay = document.getElementById("loadingOverlay");
const loadingText = document.getElementById("loadingText");

const drawnItems = new L.FeatureGroup();
map.addLayer(drawnItems);

const drawControl = new L.Control.Draw({
    edit: {
        featureGroup: drawnItems
    },
    draw: {
        polygon: {
            allowIntersection: false,
            showArea: true
        },
        polyline: false,
        rectangle: false,
        circle: false,
        circlemarker: false,
        marker: false
    }
});

map.addControl(drawControl);

map.on(L.Draw.Event.CREATED, function (event) {
    drawnItems.clearLayers();
    zoneLayer.clearLayers();

    drawnField = event.layer;
    drawnItems.addLayer(drawnField);

    fieldPolygon = drawnField.getLatLngs()[0].map(point => [
        point.lng,
        point.lat
    ]);

    drawStatus.textContent = "Field boundary selected.";

    updateAnalyzeButton();
});

map.on(L.Draw.Event.EDITED, function () {
    const layers = drawnItems.getLayers();

    if (!layers.length) {
        fieldPolygon = null;
        updateAnalyzeButton();
        return;
    }

    drawnField = layers[0];

    fieldPolygon = drawnField.getLatLngs()[0].map(point => [
        point.lng,
        point.lat
    ]);

    drawStatus.textContent = "Field boundary updated.";

    updateAnalyzeButton();
});

map.on(L.Draw.Event.DELETED, function () {
    drawnField = null;
    fieldPolygon = null;

    zoneLayer.clearLayers();

    drawStatus.textContent = "No field drawn yet — use the polygon tool on the map.";

    updateAnalyzeButton();
});

function updateAnalyzeButton() {
    analyzeBtn.disabled = !(
        fieldPolygon &&
        fieldPolygon.length >= 3 &&
        cropType.value &&
        cropStage.value
    );
}

async function loadConfig() {
    try {
        const response = await fetch("/api/config");

        if (!response.ok) {
            throw new Error("Failed to load configuration.");
        }

        const config = await response.json();

        cropType.innerHTML = '<option value="">Select crop</option>';

        config.crop_types.forEach(type => {
            const option = document.createElement("option");
            option.value = type;
            option.textContent = type;
            cropType.appendChild(option);
        });

        cropStage.innerHTML = '<option value="">Select crop stage</option>';

        config.crop_stages.forEach(stage => {
            const option = document.createElement("option");
            option.value = stage;
            option.textContent = stage;
            cropStage.appendChild(option);
        });

    } catch (error) {
        console.error(error);
        cropType.innerHTML = '<option value="">Failed to load</option>';
        cropStage.innerHTML = '<option value="">Failed to load</option>';
    }

    updateAnalyzeButton();
}

cropType.addEventListener("change", updateAnalyzeButton);
cropStage.addEventListener("change", updateAnalyzeButton);

analyzeBtn.addEventListener("click", analyzeField);

async function analyzeField() {
    if (!fieldPolygon) {
        return;
    }

    loadingOverlay.classList.remove("hidden");
    loadingText.textContent = "Retrieving Sentinel-2 satellite data...";

    analyzeBtn.disabled = true;

    resultsArea.innerHTML = `
        <div class="empty-state">
            Analyzing field...
        </div>
    `;

    try {
        const response = await fetch("/api/analyze", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                polygon: fieldPolygon,
                crop_type: cropType.value,
                crop_stage: cropStage.value
            })
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || "Field analysis failed.");
        }

        loadingText.textContent = "Rendering field-health zones...";

        renderResults(result);
        renderZones(result.zones);

        setTimeout(() => {
            loadingOverlay.classList.add("hidden");
        }, 300);

    } catch (error) {
        console.error(error);

        loadingOverlay.classList.add("hidden");

        resultsArea.innerHTML = `
            <div class="empty-state">
                <strong>Analysis failed</strong>
                <br><br>
                ${escapeHtml(error.message)}
            </div>
        `;

    } finally {
        updateAnalyzeButton();
    }
}

function renderZones(zones) {
    zoneLayer.clearLayers();

    zones.forEach(zone => {
        const geometry = zone.geometry;

        const coordinates = geometry.coordinates;

        const latLngs = coordinates.map(ring =>
            ring.map(point => [
                point[1],
                point[0]
            ])
        );

        const color = getHealthColor(zone.health_status);

        const polygon = L.polygon(latLngs, {
            color: color,
            fillColor: color,
            fillOpacity: 0.45,
            weight: 2
        });

        polygon.bindPopup(`
            <div style="min-width:180px">
                <strong>Zone ${zone.zone_id}</strong>
                <hr>

                <b>Health:</b> ${escapeHtml(zone.health_status)}
                <br>
                <b>Area:</b> ${zone.area_ha} ha
                <br><br>

                <b>NDVI:</b> ${zone.ndvi}
                <br>
                <b>NDRE:</b> ${zone.ndre}
                <br>
                <b>SAVI:</b> ${zone.savi}
                <br>
                <b>NDMI:</b> ${zone.ndmi}
            </div>
        `);

        polygon.addTo(zoneLayer);
    });

    if (zones.length > 0) {
        const bounds = zoneLayer.getBounds();

        if (bounds.isValid()) {
            map.fitBounds(bounds, {
                padding: [30, 30]
            });
        }
    }
}

function getHealthColor(status) {
    if (status === "healthy") {
        return "#1c8a5f";
    }

    if (status === "moderate") {
        return "#f2a93b";
    }

    return "#e35b4e";
}

function renderResults(data) {
    const summary = data.summary;

    const stats = summary.zone_stats;

    resultsArea.innerHTML = `
        <div class="result-card">

            <h3>Field Health Summary</h3>

            <div class="result-row">
                <span>Crop</span>
                <strong>${escapeHtml(summary.crop_type)}</strong>
            </div>

            <div class="result-row">
                <span>Stage</span>
                <strong>${escapeHtml(summary.crop_stage)}</strong>
            </div>

            <div class="result-row">
                <span>Observation</span>
                <strong>${escapeHtml(summary.observation_date)}</strong>
            </div>

            <div class="result-row">
                <span>Area</span>
                <strong>${summary.analyzed_area_ha} ha</strong>
            </div>

            <div class="result-row">
                <span>Mean NDVI</span>
                <strong>${summary.mean_ndvi}</strong>
            </div>

            <div class="result-row">
                <span>Overall Condition</span>
                <strong>${escapeHtml(summary.overall_condition)}</strong>
            </div>

            <div class="zone-stats">

                <div>
                    <span class="dot" style="background:#1c8a5f"></span>
                    Healthy
                    <strong>${stats.healthy}</strong>
                </div>

                <div>
                    <span class="dot" style="background:#f2a93b"></span>
                    Moderate
                    <strong>${stats.moderate}</strong>
                </div>

                <div>
                    <span class="dot" style="background:#e35b4e"></span>
                    Stressed
                    <strong>${stats.stressed}</strong>
                </div>

            </div>

            <div class="result-source">
                Data source: ${escapeHtml(data.data_source)}
            </div>

        </div>
    `;
}

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

const searchInput = document.getElementById("searchInput");
const searchBtn = document.getElementById("searchBtn");

searchBtn.addEventListener("click", searchPlace);

searchInput.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
        searchPlace();
    }
});

async function searchPlace() {
    const query = searchInput.value.trim();

    if (!query) {
        return;
    }

    searchBtn.disabled = true;
    searchBtn.textContent = "...";

    try {
        const response = await fetch(
            `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(query)}`
        );

        const places = await response.json();

        if (!places.length) {
            alert("Location not found.");
            return;
        }

        const place = places[0];

        const lat = parseFloat(place.lat);
        const lon = parseFloat(place.lon);

        map.setView([lat, lon], 15);

    } catch (error) {
        console.error(error);
        alert("Could not search for this location.");
    } finally {
        searchBtn.disabled = false;
        searchBtn.textContent = "Go";
    }
}

loadConfig();