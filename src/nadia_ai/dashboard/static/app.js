/* NadiaAI Dashboard — Kanban JS (no frameworks) */

const API_BASE = '';
let currentLeads = {};

// ── Fetch & Render ────────────────────────────────────────

async function fetchLeads() {
    try {
        const resp = await fetch(`${API_BASE}/api/leads`);
        const data = await resp.json();
        currentLeads = data;
        renderColumns(data.columns);
        renderStats(data.stats, data.total);
    } catch (err) {
        console.error('Failed to fetch leads:', err);
    }
}

async function fetchStats() {
    try {
        const resp = await fetch(`${API_BASE}/api/stats`);
        const stats = await resp.json();
        document.getElementById('stat-total').textContent = stats.total_leads;
        document.getElementById('stat-today').textContent = stats.new_today;
    } catch (err) {
        console.error('Failed to fetch stats:', err);
    }
}

function renderStats(stats, total) {
    document.getElementById('stat-total').textContent = total;
    document.getElementById('stat-a').textContent = stats.tier_a;
    document.getElementById('stat-b').textContent = stats.tier_b;
    document.getElementById('stat-urgent').textContent = stats.urgent;
    fetchStats(); // also gets new_today
}

function renderColumns(columns) {
    const mapping = {
        'new_to_call': 'col-new_to_call',
        'tax_followup': 'col-tax_followup',
        'urgent': 'col-urgent',
        'signed': 'col-signed',
    };

    for (const [status, elId] of Object.entries(mapping)) {
        const container = document.getElementById(elId);
        if (!container) continue;
        container.innerHTML = '';

        const leads = columns[status] || [];
        const countEl = container.parentElement.querySelector('.column-count');
        if (countEl) countEl.textContent = leads.length;

        for (const lead of leads) {
            container.appendChild(createCard(lead));
        }
    }
}

// ── Card Creation ─────────────────────────────────────────

function createCard(lead) {
    const card = document.createElement('div');
    card.className = 'lead-card';
    card.draggable = true;
    card.dataset.leadId = lead.id;

    // Name
    const displayName = lead.causante || 'Sin nombre';
    const heirLine = lead.heir_name ? `<div class="card-heir">Heredero: ${lead.heir_name}</div>` : '';

    // Address
    const address = lead.direccion || lead.localidad || '';
    const addressLine = address ? `<div class="card-address">${truncate(address, 60)}</div>` : '';

    // Days badge
    const days = lead.days_since_death;
    let daysBadge = '';
    if (days !== null && days !== undefined) {
        const cls = days >= 150 ? 'days-red' : days >= 91 ? 'days-orange' : 'days-green';
        daysBadge = `<span class="days-badge ${cls}">${days}d</span>`;
    }

    // Tier badge
    const tierCls = `badge-tier-${lead.tier.toLowerCase()}`;
    const tierBadge = `<span class="badge ${tierCls}">${lead.tier}</span>`;

    // Urgency badge
    let urgencyBadge = '';
    if (lead.urgency_phase) {
        const phaseCls = `badge-${lead.urgency_phase.replace('_', '-')}`;
        const phaseLabel = {
            'monitoring': 'Monitoreo',
            'high_motivation': 'Alta motivación',
            'urgent_distress': 'Urgente'
        }[lead.urgency_phase] || lead.urgency_phase;
        urgencyBadge = `<span class="badge ${phaseCls}">${phaseLabel}</span>`;
    }

    // Source badges
    const sources = Array.isArray(lead.sources) ? lead.sources : [];
    const sourceBadges = sources.slice(0, 3).map(s =>
        `<span class="badge badge-source">${s}</span>`
    ).join('');

    // Social link
    let socialLink = '';
    if (lead.social_profile_url) {
        socialLink = `<a href="${lead.social_profile_url}" target="_blank" class="social-link">📱 Perfil</a>`;
    }

    card.innerHTML = `
        <div class="card-top">
            <span class="card-name">${displayName}</span>
            ${daysBadge}
        </div>
        ${heirLine}
        ${addressLine}
        <div class="card-footer">
            <div>${tierBadge} ${urgencyBadge}</div>
            <div>${sourceBadges}</div>
            ${socialLink}
        </div>
    `;

    // Drag events
    card.addEventListener('dragstart', onDragStart);
    card.addEventListener('dragend', onDragEnd);

    // Click to open detail modal
    card.addEventListener('click', () => openModal(lead));

    return card;
}

function truncate(str, max) {
    return str.length > max ? str.substring(0, max) + '…' : str;
}

// ── Drag & Drop ───────────────────────────────────────────

let draggedId = null;

function onDragStart(e) {
    draggedId = e.currentTarget.dataset.leadId;
    e.currentTarget.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
}

function onDragEnd(e) {
    e.currentTarget.classList.remove('dragging');
    document.querySelectorAll('.column-body').forEach(col => {
        col.classList.remove('drag-over');
    });
}

// Set up column drop targets
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.column-body').forEach(col => {
        col.addEventListener('dragover', e => {
            e.preventDefault();
            col.classList.add('drag-over');
        });

        col.addEventListener('dragleave', () => {
            col.classList.remove('drag-over');
        });

        col.addEventListener('drop', async e => {
            e.preventDefault();
            col.classList.remove('drag-over');

            if (!draggedId) return;

            const status = col.id.replace('col-', '');
            try {
                await fetch(`${API_BASE}/api/leads/${draggedId}/status`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status }),
                });
                fetchLeads(); // Refresh
            } catch (err) {
                console.error('Status update failed:', err);
            }
            draggedId = null;
        });
    });

    // Initial load
    fetchLeads();

    // Auto-refresh every 60s
    setInterval(fetchLeads, 60000);

    // Modal close
    document.getElementById('modal-close').addEventListener('click', closeModal);
    document.getElementById('modal-overlay').addEventListener('click', e => {
        if (e.target === e.currentTarget) closeModal();
    });
});

// ── Modal ─────────────────────────────────────────────────

function openModal(lead) {
    const content = document.getElementById('modal-content');
    const sources = Array.isArray(lead.sources) ? lead.sources.join(', ') : '';

    content.innerHTML = `
        <h3>${lead.causante || 'Sin nombre'}</h3>

        ${lead.heir_name ? `<div class="modal-field"><label>Heredero principal</label><div class="value">${lead.heir_name}</div></div>` : ''}

        <div class="modal-field"><label>Dirección</label><div class="value">${lead.direccion || '-'}</div></div>
        <div class="modal-field"><label>Localidad</label><div class="value">${lead.localidad || '-'}</div></div>
        <div class="modal-field"><label>Ref. Catastral</label><div class="value">${lead.referencia_catastral || '-'}</div></div>

        <div class="modal-field"><label>Fecha fallecimiento</label><div class="value">${lead.fecha_fallecimiento || lead.date_of_death || '-'}</div></div>
        <div class="modal-field"><label>Días desde fallecimiento</label><div class="value">${lead.days_since_death !== null ? lead.days_since_death + ' días' : '-'}</div></div>
        <div class="modal-field"><label>Plazo fiscal</label><div class="value">${lead.tax_deadline || '-'}</div></div>

        <div class="modal-field"><label>Tier</label><div class="value">${lead.tier}</div></div>
        <div class="modal-field"><label>Fuentes</label><div class="value">${sources || '-'}</div></div>
        <div class="modal-field"><label>Detectado</label><div class="value">${lead.first_seen_at || '-'}</div></div>

        ${lead.social_profile_url ? `<div class="modal-field"><label>Perfil social</label><div class="value"><a href="${lead.social_profile_url}" target="_blank" class="social-link">${lead.social_profile_url}</a></div></div>` : ''}

        <div class="modal-field"><label>Outreach</label><div class="value">${lead.outreach_allowed ? 'Sí' : 'No'} ${lead.outreach_notes ? '— ' + lead.outreach_notes : ''}</div></div>

        <div class="modal-notes">
            <label>Notas</label>
            <textarea id="modal-notes-input">${lead.notas || ''}</textarea>
            <button class="btn-save" onclick="saveNotes(${lead.id})">Guardar notas</button>
        </div>
    `;

    document.getElementById('modal-overlay').classList.add('active');
}

function closeModal() {
    document.getElementById('modal-overlay').classList.remove('active');
}

async function saveNotes(leadId) {
    const notes = document.getElementById('modal-notes-input').value;
    try {
        await fetch(`${API_BASE}/api/leads/${leadId}/notes`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ notes }),
        });
        closeModal();
        fetchLeads();
    } catch (err) {
        console.error('Failed to save notes:', err);
    }
}
