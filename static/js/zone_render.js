// Shared rendering helpers used by field_detail.js (and previously demo.js's
// logic) so the zone-coloring / summary-card markup lives in one place.

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function getHealthColor(status) {
  if (status === "healthy") return "#1c8a5f";
  if (status === "moderate") return "#f2a93b";
  return "#e35b4e";
}

/**
 * Draws zone polygons (GeoJSON, [lon, lat] rings) onto a Leaflet
 * featureGroup, color-coded by health_status, with a click popup showing
 * spectral indices. Returns the featureGroup's bounds (or null).
 */
function renderZoneLayer(map, zoneLayer, zones) {
  zoneLayer.clearLayers();

  (zones || []).forEach((zone) => {
    const coordinates = zone.geometry.coordinates;
    const latLngs = coordinates.map((ring) => ring.map((pt) => [pt[1], pt[0]]));
    const color = getHealthColor(zone.health_status);

    const polygon = L.polygon(latLngs, {
      color,
      fillColor: color,
      fillOpacity: 0.45,
      weight: 2,
    });

    let hyperHtml = "";
    if (zone.hyperspectral) {
      hyperHtml = `
        <br>
        <b>Hyperspectral confirm:</b> ${zone.hyperspectral.verified ? "Yes" : "Partial"}
        (${zone.hyperspectral.confidence_pct}% confidence)
      `;
    }

    polygon.bindPopup(`
      <div style="min-width:190px">
        <strong>Zone ${zone.zone_id}</strong>
        <hr>
        <b>Health:</b> ${escapeHtml(zone.health_status)}<br>
        <b>Area:</b> ${zone.area_ha} ha
        <br><br>
        <b>NDVI:</b> ${zone.ndvi}<br>
        <b>NDRE:</b> ${zone.ndre}<br>
        <b>SAVI:</b> ${zone.savi}<br>
        <b>NDMI:</b> ${zone.ndmi}
        ${hyperHtml}
      </div>
    `);

    polygon.addTo(zoneLayer);
  });

  if (zones && zones.length) {
    const bounds = zoneLayer.getBounds();
    if (bounds.isValid()) return bounds;
  }
  return null;
}

/** Builds the field-health summary card markup shown beside the map. */
function buildSummaryCardHtml(summary, dataSource) {
  const stats = summary.zone_stats || {};
  return `
    <div class="result-card">
      <h3>Field Health Summary</h3>

      <div class="result-row"><span>Crop</span><strong>${escapeHtml(summary.crop_type)}</strong></div>
      <div class="result-row"><span>Stage</span><strong>${escapeHtml(summary.crop_stage)}</strong></div>
      <div class="result-row"><span>Observation</span><strong>${escapeHtml(summary.observation_date)}</strong></div>
      <div class="result-row"><span>Area</span><strong>${summary.analyzed_area_ha} ha</strong></div>
      <div class="result-row"><span>Mean NDVI</span><strong>${summary.mean_ndvi ?? "—"}</strong></div>
      <div class="result-row"><span>Overall Condition</span><strong>${escapeHtml(summary.overall_condition)}</strong></div>

      <div class="zone-stats">
        <div><span class="dot" style="background:#1c8a5f"></span>Healthy<br><strong>${stats.healthy ?? 0}</strong></div>
        <div><span class="dot" style="background:#f2a93b"></span>Moderate<br><strong>${stats.moderate ?? 0}</strong></div>
        <div><span class="dot" style="background:#e35b4e"></span>Stressed<br><strong>${stats.stressed ?? 0}</strong></div>
      </div>

      ${dataSource ? `<div class="result-source">Data source: ${escapeHtml(dataSource)}</div>` : ""}
    </div>
  `;
}