/* NadiaAI Dashboard — Spain-wide Leads Engine */

const API_BASE = '';
let currentPage = 0;
const PAGE_SIZE = 50;
let totalLeadsCount = 0;
let allLeads = [];
let activeRegions = new Set(['all']);

async function fetchLeads(append = false) {
    try {
        if (!append) {
            currentPage = 0;
            allLeads = [];
        }
        
        const offset = currentPage * PAGE_SIZE;
        const tier = document.getElementById('filter-tier').value;
        const resp = await fetch(`${API_BASE}/api/leads?limit=${PAGE_SIZE}&offset=${offset}&tier=${tier}`);
        const data = await resp.json();
        
        totalLeadsCount = data.total;
        
        // Flatten Kanban columns into a single array
        const newLeads = [
            ...(data.columns.new_to_call || []),
            ...(data.columns.tax_followup || []),
            ...(data.columns.urgent || []),
            ...(data.columns.signed || [])
        ];

        // Compute lead scores
        newLeads.forEach(lead => { lead._score = computeScore(lead); });
        
        if (append) {
            allLeads = [...allLeads, ...newLeads];
        } else {
            allLeads = newLeads;
        }

        renderStats(data.stats, data.total);
        buildRegionCheckboxes();
        renderTable();
        
        // Update pagination UI
        const info = document.getElementById('pagination-info');
        info.textContent = `Mostrando ${allLeads.length} de ${totalLeadsCount} leads`;
        
        const btn = document.getElementById('load-more-btn');
        if (allLeads.length >= totalLeadsCount) {
            btn.style.display = 'none';
        } else {
            btn.style.display = 'block';
        }
        
    } catch (err) {
        console.error('Failed to fetch leads:', err);
    }
}

async function loadMore() {
    currentPage++;
    await fetchLeads(true);
}

/* ── Scoring ─────────────────────────────────────── */
function computeScore(lead) {
    let score = 0;

    // Tier weight
    if (lead.tier === 'A') score += 40;
    else if (lead.tier === 'B') score += 20;
    else if (lead.tier === 'X') score += 10;

    // Has heir extracted
    if (lead.heir_name && lead.heir_name !== '' && !lead.heir_name.toLowerCase().includes('no extra')) score += 15;

    // Has address
    if (lead.direccion && lead.direccion !== '') score += 10;

    // Has death date (enables plusvalía clock)
    if (lead.date_of_death || lead.fecha_fallecimiento) score += 5;

    // Urgency bonus (close to 180-day tax deadline)
    const days = lead.days_since_death;
    if (days !== null && days !== undefined) {
        if (days >= 91 && days <= 180) score += 20;  // High motivation zone
        else if (days >= 150) score += 15;            // Urgent/distress
        else if (days <= 90) score += 5;              // Fresh lead
    }

    // Social profile enriched
    if (lead.social_profile_url) score += 10;

    return Math.min(score, 100);
}

/* ── Stats ────────────────────────────────────────── */
function renderStats(stats, total) {
    document.getElementById('stat-total').textContent = total;
    document.getElementById('stat-a').textContent = stats.tier_a;
    document.getElementById('stat-b').textContent = stats.tier_b;
    document.getElementById('stat-urgent').textContent = stats.urgent;

    // Count unique regions
    const regions = new Set(allLeads.map(l => l.region || l.localidad || 'Desconocida'));
    document.getElementById('stat-regions').textContent = regions.size;
}

/* ── Region Checkboxes ───────────────────────────── */
function buildRegionCheckboxes() {
    const container = document.getElementById('region-filter');
    
    // Collect all unique regions
    const regionCounts = {};
    allLeads.forEach(lead => {
        const r = lead.region || lead.localidad || 'Desconocida';
        regionCounts[r] = (regionCounts[r] || 0) + 1;
    });

    // Sort by count descending
    const sorted = Object.entries(regionCounts).sort((a, b) => b[1] - a[1]);

    // Clear existing dynamic checkboxes (keep the "Todas" and label)
    const existing = container.querySelectorAll('.region-check-dynamic');
    existing.forEach(el => el.remove());

    sorted.forEach(([region, count]) => {
        const label = document.createElement('label');
        label.className = 'region-check region-check-dynamic';
        label.innerHTML = `<input type="checkbox" value="${region}" checked> ${region} <small>(${count})</small>`;
        container.appendChild(label);
    });

    // Wire up "Todas" checkbox
    const allCheck = container.querySelector('input[value="all"]');
    allCheck.addEventListener('change', () => {
        const checks = container.querySelectorAll('.region-check-dynamic input');
        checks.forEach(c => { c.checked = allCheck.checked; });
        updateActiveRegions();
        renderTable();
    });

    // Wire up individual region checkboxes
    container.querySelectorAll('.region-check-dynamic input').forEach(cb => {
        cb.addEventListener('change', () => {
            updateActiveRegions();
            renderTable();
        });
    });
}

function updateActiveRegions() {
    const container = document.getElementById('region-filter');
    const checks = container.querySelectorAll('.region-check-dynamic input');
    activeRegions = new Set();
    
    let allChecked = true;
    checks.forEach(c => {
        if (c.checked) activeRegions.add(c.value);
        else allChecked = false;
    });

    // Update "Todas" state
    container.querySelector('input[value="all"]').checked = allChecked;

    if (activeRegions.size === 0 || allChecked) {
        activeRegions.add('all');
    }
}

/* ── Table Rendering ──────────────────────────────── */
function renderTable() {
    const tbody = document.getElementById('leads-tbody');
    const searchTerm = document.getElementById('search-input').value.toLowerCase();
    const sortBy = document.getElementById('sort-by').value;

    // Filter
    let filtered = allLeads.filter(lead => {
        // Search
        const text = `${lead.causante} ${lead.direccion} ${lead.heir_name} ${lead.region} ${lead.localidad} ${lead.juzgado || ''}`.toLowerCase();
        if (searchTerm && !text.includes(searchTerm)) return false;

        // Region
        if (!activeRegions.has('all')) {
            const leadRegion = lead.region || lead.localidad || 'Desconocida';
            if (!activeRegions.has(leadRegion)) return false;
        }

        return true;
    });

    // Sort
    filtered.sort((a, b) => {
        switch (sortBy) {
            case 'score': return b._score - a._score;
            case 'days_desc': return (b.days_since_death || 0) - (a.days_since_death || 0);
            case 'days_asc': return (a.days_since_death || 9999) - (b.days_since_death || 9999);
            case 'name': return (a.causante || '').localeCompare(b.causante || '');
            default: return b._score - a._score;
        }
    });

    // Update count
    document.getElementById('results-count').textContent = `Mostrando ${filtered.length} de ${allLeads.length} leads`;

    tbody.innerHTML = '';

    for (const lead of filtered) {
        const tr = document.createElement('tr');
        const days = lead.days_since_death;
        
        // Row class for urgency
        if (days !== null && days >= 150) tr.classList.add('urgent-row');
        else if (lead.tier === 'A') tr.classList.add('hot-row');

        // Score badge
        const scoreClass = lead._score >= 60 ? 'score-high' : lead._score >= 30 ? 'score-med' : 'score-low';

        // Days display
        let daysHtml = '<span class="days-normal">-</span>';
        if (days !== null && days !== undefined) {
            const daysClass = days >= 150 ? 'days-urgent' : days >= 91 ? 'days-high' : 'days-normal';
            daysHtml = `<span class="days-value ${daysClass}">${days}d</span>`;
        }

        // Region
        const region = lead.region || lead.localidad || 'Desconocida';

        // Sources
        let sourcesHtml = '';
        const sources = Array.isArray(lead.sources) ? lead.sources : [];
        sources.forEach(s => { sourcesHtml += `<span class="source-tag">${s}</span>`; });
        
        let sourceUrls = [];
        try { sourceUrls = JSON.parse(lead.source_urls || '[]'); } catch(e) {}
        if (sourceUrls.length > 0) {
            sourcesHtml += ` <a href="${sourceUrls[0]}" target="_blank" class="source-link">[Ver]</a>`;
        }

        // Heir display
        const heirHtml = lead.heir_name && lead.heir_name.trim()
            ? `<span class="heir-badge">${lead.heir_name}</span>`
            : '<span class="heir-missing">No extraído</span>';

        // Detection date
        const detDate = lead.first_seen_at ? lead.first_seen_at.split('T')[0].split(' ')[0] : '-';

        tr.innerHTML = `
            <td class="col-score"><span class="score-badge ${scoreClass}">${lead._score}</span></td>
            <td><span class="causante-name">${lead.causante || 'Sin nombre'}</span></td>
            <td>${heirHtml}</td>
            <td><span class="region-badge">${region}</span></td>
            <td>${daysHtml}</td>
            <td><span class="badge badge-tier-${lead.tier.toLowerCase()}">${lead.tier}</span></td>
            <td><span class="address-text">${lead.direccion || '-'}</span></td>
        `;

        // Detail row
        const detailTr = document.createElement('tr');
        detailTr.className = 'detail-row';
        detailTr.style.display = 'none';

        // Parse heir list
        let heirsList = 'N/A';
        try {
            const h = JSON.parse(lead.heir_names_json || '[]');
            if (h.length > 0) heirsList = h.join(', ');
        } catch(e) {}

        // Phantombuster
        const pbLink = lead.social_profile_url
            ? `<a href="${lead.social_profile_url}" target="_blank" class="social-link">📱 Perfil Social</a>`
            : '<em style="color:#475569">Pendiente de Phantombuster</em>';

        // Source links list
        let allLinksHtml = '';
        sourceUrls.forEach((url, i) => {
            allLinksHtml += `<a href="${url}" target="_blank" class="source-link">[Enlace ${i + 1}]</a> `;
        });

        detailTr.innerHTML = `
            <td colspan="8">
                <div class="detail-container">
                    <div class="detail-grid">
                        <div class="detail-section">
                            <h4>🏠 Propiedad</h4>
                            <p><strong>Dirección:</strong> ${lead.direccion || 'No disponible'}</p>
                            <p><strong>Superficie:</strong> ${lead.m2 ? lead.m2 + ' m²' : 'N/A'}</p>
                            <p><strong>Año Constr.:</strong> ${lead.year_built || 'N/A'}</p>
                            <p><strong>Uso:</strong> ${lead.use_class || 'N/A'}</p>
                            <p><strong>Barrio:</strong> ${lead.neighborhood || 'N/A'}</p>
                            <p><strong>Ref. Catastral:</strong> ${lead.referencia_catastral || 'N/A'}</p>
                        </div>
                        <div class="detail-section">
                            <h4>⚖️ Herencia</h4>
                            <p><strong>Fecha Fallecimiento:</strong> ${lead.fecha_fallecimiento || lead.date_of_death || 'No extraída'}</p>
                            <p><strong>Lugar Fallecimiento:</strong> ${lead.lugar_fallecimiento || 'N/A'}</p>
                            <p><strong>Todos los herederos:</strong> ${heirsList}</p>
                            <p><strong>Juzgado / Notario:</strong> ${lead.juzgado || 'N/A'}</p>
                            <p><strong>Límite Fiscal (Plusvalía):</strong> ${lead.tax_deadline || 'N/A'}</p>
                            <p><strong>Fase de Urgencia:</strong> ${formatUrgency(lead.urgency_phase)}</p>
                        </div>
                        <div class="detail-section">
                            <h4>📞 Contacto</h4>
                            <p>${pbLink}</p>
                            <p><strong>Obras Recientes:</strong> ${lead.obras_recientes || 'Ninguna detectada'}</p>
                            <p><strong>Notas IA:</strong> ${lead.outreach_notes || 'OK para contactar'}</p>
                        </div>
                        <div class="detail-section">
                            <h4>📋 Fuentes</h4>
                            <p>${allLinksHtml || 'Sin enlaces'}</p>
                            <p><strong>Región:</strong> ${region}</p>
                            <p><strong>Primera detección:</strong> ${lead.first_seen_at || 'N/A'}</p>
                            <p><strong>Última actualiz.:</strong> ${lead.last_updated_at || 'N/A'}</p>
                        </div>
                    </div>
                </div>
            </td>
        `;

        // Toggle expand
        tr.addEventListener('click', () => {
            const isVisible = detailTr.style.display === 'table-row';
            detailTr.style.display = isVisible ? 'none' : 'table-row';
            tr.classList.toggle('expanded', !isVisible);
        });

        tbody.appendChild(tr);
        tbody.appendChild(detailTr);
    }
}

function formatUrgency(phase) {
    switch (phase) {
        case 'monitoring': return '🟢 Monitoreo (0-90 días)';
        case 'high_motivation': return '🟡 Alta Motivación (91-150 días)';
        case 'urgent_distress': return '🔴 Urgente/Distress (150+ días)';
        default: return 'Sin datos';
    }
}

/* ── Init ─────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
    fetchLeads();
    
    document.getElementById('search-input').addEventListener('input', renderTable);
    document.getElementById('filter-tier').addEventListener('change', () => fetchLeads(false));
    document.getElementById('sort-by').addEventListener('change', renderTable);
    document.getElementById('load-more-btn').addEventListener('click', loadMore);
    
    // Auto-refresh every 60s
    setInterval(() => fetchLeads(false), 60000);
});
