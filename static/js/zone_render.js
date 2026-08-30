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

// zone_id -> Leaflet layer, rebuilt every renderZoneLayer() call, so other
// scripts (the zones-tab side list) can look up / re-open a zone's popup
// without re-implementing the polygon draw logic.
const zoneLayerRegistry = {};

/**
 * Draws zone polygons (GeoJSON, [lon, lat] rings) onto a Leaflet
 * featureGroup, color-coded by health_status, with a click popup showing
 * spectral indices. Returns the featureGroup's bounds (or null).
 */
function renderZoneLayer(map, zoneLayer, zones) {
  zoneLayer.clearLayers();
  Object.keys(zoneLayerRegistry).forEach((k) => delete zoneLayerRegistry[k]);

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
    zoneLayerRegistry[zone.zone_id] = polygon;
  });

  if (zones && zones.length) {
    const bounds = zoneLayer.getBounds();
    if (bounds.isValid()) return bounds;
  }
  return null;
}

/** Opens a zone's existing map popup/marker, used by the zone list rows. */
function focusZone(map, zoneId) {
  const layer = zoneLayerRegistry[zoneId];
  if (!layer) return;
  const bounds = layer.getBounds();
  if (bounds.isValid()) map.fitBounds(bounds, { padding: [60, 60], maxZoom: 18 });
  layer.openPopup();
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

/** Compact clickable zone list shown beside the map on the Zones tab. */
function buildZoneListHtml(zones) {
  if (!zones || !zones.length) {
    return `<div class="empty-state">No zones yet — run an analysis.</div>`;
  }
  return zones
    .map((zone) => {
      const color = getHealthColor(zone.health_status);
      const conf = zone.hyperspectral ? `${zone.hyperspectral.confidence_pct}% conf.` : "";
      return `
        <div class="zone-table-row" data-zone-id="${zone.zone_id}">
          <span class="ztr-dot" style="background:${color}"></span>
          <span class="ztr-main">
            <span class="ztr-title">Zone ${zone.zone_id} · ${escapeHtml(zone.health_status)}</span>
            <span class="ztr-detail">${zone.area_ha} ha · NDVI ${zone.ndvi}</span>
          </span>
          <span class="ztr-conf">${escapeHtml(conf)}</span>
        </div>`;
    })
    .join("");
}

/** Full spectral-index table for the Evidence tab (raw data, third tier). */
function buildEvidenceZoneTableHtml(zones) {
  if (!zones || !zones.length) {
    return `<div class="empty-state">No analysis yet.</div>`;
  }
  const rows = zones
    .map(
      (z) => `
      <tr>
        <td>Zone ${z.zone_id}</td>
        <td>${escapeHtml(z.health_status)}</td>
        <td>${z.area_ha}</td>
        <td>${z.ndvi ?? "—"}</td>
        <td>${z.ndre ?? "—"}</td>
        <td>${z.savi ?? "—"}</td>
        <td>${z.ndmi ?? "—"}</td>
        <td>${z.hyperspectral ? z.hyperspectral.confidence_pct + "%" : "—"}</td>
      </tr>`
    )
    .join("");
  return `
    <table class="evidence-zone-table">
      <thead><tr><th>Zone</th><th>Health</th><th>Area (ha)</th><th>NDVI</th><th>NDRE</th><th>SAVI</th><th>NDMI</th><th>Hyperspectral</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}