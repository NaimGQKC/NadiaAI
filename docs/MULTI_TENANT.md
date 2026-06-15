# Multi-tenant lead scoping

One NadiaAI deployment can serve many agents at once.  Each agent ("tenant")
owns a territory (list of provinces and/or specific localidades).  Every
worklist or outreach pack is scoped to that territory — an agent in Zaragoza
never sees leads from Madrid.

---

## 1. Add an agent

```bash
python -m tools.tenant --add \
  --name "Ana Muro" \
  --agency "RE/MAX Zaragoza Norte" \
  --phone "600123456" \
  --territory "Zaragoza,Huesca"
```

`--territory` is a comma-separated list of **provinces** and/or **specific towns**.
Values are accent-stripped before comparison, so "Málaga" and "Malaga" both work.

```bash
# Narrow to a handful of towns instead of a whole province:
python -m tools.tenant --add --name "Pedro Gil" \
  --territory "Calatayud,Ejea,Tauste"
```

---

## 2. List agents

```bash
python -m tools.tenant --list             # all tenants
python -m tools.tenant --list --active-only
```

Output example:

```
2 tenants:
  id=2  'Pedro Gil'  —  tel=—  [active]  territory=["Calatayud", "Ejea", "Tauste"]  created=2026-06-15
  id=1  'Ana Muro'   RE/MAX Zaragoza Norte  tel=600123456  [active]  territory=["Zaragoza", "Huesca"]  created=2026-06-15
```

---

## 3. Update or disable

```bash
# Replace territory:
python -m tools.tenant --set-territory 1 --territory "Zaragoza,Huesca,Teruel"

# Soft-disable (leads_for_tenant still works; exclude from automated runs manually):
python -m tools.tenant --disable 2

# Re-enable:
python -m tools.tenant --enable 2
```

---

## 4. Territory matching rules

| `leads.region` | `leads.localidad` | Included if … |
|---|---|---|
| "Zaragoza" | "Calatayud" | territory contains "Zaragoza" |
| "" (blank) | "Ejea" | territory contains "Ejea" |
| "Madrid" | "Madrid" | territory does NOT contain "Madrid" → excluded |

`region` is the province rollup set at write time by `merge.province_for_localidad`.
`localidad` is the raw city name.  Both are accent-stripped before comparison so
"Málaga" == "Malaga".

Territories may overlap — two agents in the same province both receive those leads.
This is intentional (referral / hand-off scenarios).

---

## 5. Integration — scoping existing exports per tenant

Neither `tools/export_call_list.py` nor `tools/generate_outreach.py` is modified.
Instead, wrap their lead-fetching step with `leads_for_tenant`:

### export_call_list.py

```python
from nadia_ai.tenancy import get_tenant, leads_for_tenant

# Replace the existing `conn.execute("SELECT * FROM leads")` block:
tenant = get_tenant(conn, tenant_id)          # e.g. tenant_id=1
leads = leads_for_tenant(conn, tenant)
```

The rest of the `build()` function (dedup, sort, Excel write) is unchanged.

### generate_outreach.py — `_inheritance_cohort`

```python
from nadia_ai.tenancy import get_tenant, leads_for_tenant

# Instead of the raw SQL query, use:
tenant = get_tenant(conn, tenant_id)
leads = leads_for_tenant(
    conn,
    tenant,
    extra_where="AND (TRIM(COALESCE(causante,'')) <> '' OR TRIM(COALESCE(heir_name,'')) <> '')",
    order_sql=(
        "CASE WHEN edict_window_days IS NOT NULL AND edict_window_days >= 0 THEN 0 ELSE 1 END,"
        "edict_window_days ASC,"
        "first_seen_at DESC"
    ),
)
leads = leads[:limit]
```

### Suggested wiring pattern

Both scripts already accept a `db_path` argument.  Add a `tenant_id` argument
alongside it and resolve the tenant at the top of `build()`:

```python
def build(..., tenant_id: int | None = None):
    conn = sqlite3.connect(db_path)
    ...
    if tenant_id is not None:
        from nadia_ai.tenancy import get_tenant, leads_for_tenant
        tenant = get_tenant(conn, tenant_id)
        if tenant is None:
            raise ValueError(f"Tenant id={tenant_id} not found")
        leads = leads_for_tenant(conn, tenant)
    else:
        leads = [dict(r) for r in conn.execute("SELECT * FROM leads").fetchall()]
    ...
```

When `tenant_id` is omitted the scripts behave exactly as before — no regression
for the single-agent use-case.

---

## 6. API reference (nadia_ai.tenancy)

| Function | Purpose |
|---|---|
| `init_tenancy_schema(conn)` | Create `tenants` table + indexes (idempotent) |
| `add_tenant(conn, *, name, agency, phone, territory, active)` | Insert tenant; returns `int` id |
| `get_tenant(conn, tenant_id)` | Fetch by id; returns `dict` or `None` |
| `list_tenants(conn, *, active_only)` | All tenants, newest first |
| `set_tenant_active(conn, tenant_id, active)` | Toggle enabled/disabled |
| `update_territory(conn, tenant_id, territory)` | Replace territory list |
| `leads_for_tenant(conn, tenant, *, order_sql, extra_where, params)` | Scoped lead list |
