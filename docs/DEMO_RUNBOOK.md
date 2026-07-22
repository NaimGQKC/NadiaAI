# Demo runbook — heir contact enrichment

Turnkey steps to run the pipeline fresh and record the live email-enrichment demo.
Everything is already coded and pushed; this is the operator sequence.

## 0. One-time, tonight
- **Merge `claude/client-progress-review-8rbzbt` → `main`** so the scheduled pipeline
  and all tools run tonight's fixes (accent decode, surname reconstruction, hardened
  name quality). Without this, tomorrow's run uses old code.
- Have the **PDL API key** ready (paste it at dispatch time; reroll after).

## 1. Morning — start PEDRO
- Open the laptop; in PowerShell:
  ```powershell
  cd C:\Users\anaim\actions-runner
  .\run.cmd          # wait for "Listening for Jobs"
  ```

## 2. Run the full pipeline (fresh, clean data)
- GitHub → Actions → **NadiaAI Daily Pipeline (self-hosted)** → Run workflow.
- This scrapes + extracts with the new fixes (correct accents, reconstructed
  surnames, no institutions/boilerplate stored as heirs) and refreshes the DB.
- ~8 min. Does **not** spend PDL credits.

## 3. (Optional) Preview the clean cohort — 0 credits
- Actions → **Test heir-phone coverage** → Run workflow:
  - `api_key` = PDL key
  - `provider` = `pdl:aragon:dry`   ← dry run, lists who would be enriched
  - `sample` = `50`
- Confirms the cohort looks clean on camera before spending anything.

## 4. Record the live enrichment (the money shot)
- Actions → **Test heir-phone coverage** → Run workflow:
  - `api_key` = PDL key
  - `provider` = `pdl:aragon`   ← real run (no `:dry`); Aragón only
  - `sample` = `30`             ← caps credits (~20–30 max)
- Open the running job → expand **“Run heir-phone coverage test”** and screen-record.
- Output is demo-formatted: banner → each heir → real email, then a results panel:
  ```
  ══════════════════════════════════════════════════════════════
    NadiaAI · Heir Contact Enrichment · Aragon
  ══════════════════════════════════════════════════════════════
    20 heirs with a quality name … Resolving personal emails via People Data Labs…

    ✓ José Javier Gil Barranco          Zaragoza     →  jjgil…@gmail.com
    · Charo Lahoz                       Zaragoza     →  (no email on file)
    …
    RESULTS
    Emails found ......... 4/20 (20%)
  ```

## Scope knobs (provider string, no redeploy needed)
- Region: `pdl:<ccaa>` — e.g. `pdl:madrid`, `pdl:cataluna`, or omit for all Spain.
- Tier: add `:a` (address-bearing only) — e.g. `pdl:aragon:a`.
- Dry run: add `:dry` — lists selected heirs, 0 credits.
- Credit ceiling is `sample` (heirs attempted) capped by `HEIR_MAX_MATCHES` (330).

## Notes
- Every run skips heirs that already have an email, so re-runs don't double-spend.
- `contact_email` is written with `contact_source='pdl'`, low confidence; the matched
  LinkedIn URL is printed so identity can be spot-checked.
