# Conversion Tracking

How NadiaAI logs every outreach attempt and tracks whether it converts a lead into a listed property.

## Why this exists

The pipeline generates hundreds of leads per week. Without a log we cannot know:

- Whether a lead was already contacted (and when).
- Whether the contact converted into a listing.
- What the actual response / interest / listing rates are.

The `outreach_log` table answers all three questions and also enforces the no-double-contact rule (GDPR principle of data minimisation and the agent's practical interest in not annoying the same person twice).

---

## Schema

`outreach_log` is created by `nadia_ai.db.init_db` and extended by `nadia_ai.outreach_log.init_outreach_log_schema`:

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | Auto |
| `lead_id` | INTEGER | FK → `leads.id` |
| `action` | TEXT | Always `'outreach'` |
| `channel` | TEXT | `call`, `whatsapp`, `letter` |
| `tipo` | TEXT | Lead type (`FSBO`, `BOE-N`, `Heraldo`, …) |
| `legal_basis` | TEXT | Defaults to `6.1.f RGPD` |
| `notes` | TEXT | Free-text |
| `created_at` | TEXT | ISO-8601 UTC (= `sent_at`) |
| `outcome` | TEXT | See valid outcomes below |
| `outcome_at` | TEXT | ISO-8601 UTC, set by `record_outcome` |

**Valid outcomes:** `pending`, `no_answer`, `not_interested`, `interested`, `listed`, `opted_out`

---

## API — `nadia_ai.outreach_log`

### `log_outreach(conn, lead_id, channel, tipo, *, cooldown_days=30, notes='', legal_basis='6.1.f RGPD') -> bool`

Records an outreach attempt. **Idempotent within the cooldown window**: if the same `(lead_id, channel)` pair was already logged within the last `cooldown_days` days the call is a no-op and returns `False`. Otherwise inserts a row with `outcome='pending'` and returns `True`.

```python
from nadia_ai.outreach_log import log_outreach

logged = log_outreach(conn, lead_id=42, channel="call", tipo="FSBO")
# True  → logged now
# False → already contacted within 30 days, skipped
```

### `record_outcome(conn, lead_id, outcome, notes='') -> bool`

Updates the **most-recent** outreach row for the lead with an outcome and stamps `outcome_at`. Returns `False` if no outreach row exists yet.

When `outcome == 'opted_out'` the lead's `causante` / `heir_name` and `contact_phone` / `contact_email` are automatically written to `suppression_list` via `nadia_ai.suppression.add_suppression` so they can never reappear in a future worklist.

```python
from nadia_ai.outreach_log import record_outcome

record_outcome(conn, lead_id=42, outcome="interested", notes="cita el martes")
record_outcome(conn, lead_id=17, outcome="opted_out")
# → also adds to suppression_list automatically
```

### `funnel(conn) -> dict`

Returns conversion-funnel counts and rates for all logged leads:

```python
from nadia_ai.outreach_log import funnel

f = funnel(conn)
# {
#   'contacted':     38,
#   'responded':     14,
#   'interested':     6,
#   'listed':         2,
#   'opted_out':      1,
#   'response_rate':  0.3684,
#   'interest_rate':  0.4286,
#   'listing_rate':   0.3333,
# }
```

Funnel stages:

| Stage | Definition |
|-------|-----------|
| contacted | Distinct leads with at least one outreach row |
| responded | Contacted leads whose latest outcome is not `pending` or `no_answer` |
| interested | Outcome is `interested` or `listed` |
| listed | Outcome is `listed` |

---

## CLI — `tools/mark_outcome.py`

```bash
# Record an outcome
python -m tools.mark_outcome --lead-id 42 --outcome interested
python -m tools.mark_outcome --lead-id 17 --outcome opted_out --notes "queja telefónica"

# List recent entries (last 50)
python -m tools.mark_outcome --list

# Print funnel stats
python -m tools.mark_outcome --funnel
```

---

## Opt-out → suppression flow

```
record_outcome(conn, lead_id, "opted_out")
        │
        └─► looks up leads.causante / heir_name / contact_phone / contact_email
                │
                └─► suppression.add_suppression(conn, name=..., phone=..., ...)
                        │
                        └─► suppression_list row written (idempotent)
                                │
                                └─► future filter_suppressed() calls exclude this lead
```

---

## Integration with `generate_outreach.build()`

Add these lines to `tools/generate_outreach.py` immediately after rendering each lead — **do not edit `build()` itself**, just insert these calls in the two render loops:

```python
# At the top of generate_outreach.py, add:
from nadia_ai.outreach_log import log_outreach

# Inside build(), after conn = sqlite3.connect(db_path) and before conn.close():

# ── FSBO sheet (Sheet 1) ──────────────────────────────────────────────────
for lead in fsbo:
    o = render_outreach(lead, use_llm=use_llm)
    # ... existing _write_row call ...
    if lead.get("id"):
        log_outreach(conn, lead["id"], channel="call", tipo=o["tipo"])

# ── Inheritance sheet (Sheet 2) ───────────────────────────────────────────
for lead in inh:
    o = render_outreach(lead, use_llm=use_llm)
    # ... existing _write_row call ...
    if lead.get("id"):
        log_outreach(conn, lead["id"], channel="letter", tipo=o["tipo"])
```

`log_outreach` is idempotent so running `build()` twice on the same day is safe — the second run skips already-logged leads.
