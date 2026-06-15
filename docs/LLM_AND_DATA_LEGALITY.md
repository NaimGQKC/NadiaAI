# LLM Choice & Data Legality — Decision Notes

_Last updated: 2026-06-13. Not legal advice — see the disclaimer at the bottom._

> **CURRENT SETUP (supersedes section 1 below):** the pipeline uses **two
> OpenAI-compatible LLMs, one job each** — a cheap model for text→JSON extraction
> (`EXTRACTION_*` env vars) and **Perplexity Sonar** for search-native contact
> discovery. Claude/Gemini are no longer the production path. The extraction
> endpoint is provider-neutral; the **current deployment points at MiniMax `minimax-m3`
> via OpenRouter** (`https://openrouter.ai/api/v1`), defaulting to DeepSeek if the
> env vars are unset. The OpenRouter free trial is credit-capped, so extraction has
> an adaptive `max_tokens`/preflight guard and a "defer, don't regex-clobber" failure
> mode (see `utils/extraction.py`). Section 1's "why not local LLM" reasoning still
> holds (a cloud API works in CI; local Ollama does not); only the provider changed.
> **Cross-border note:** MiniMax/DeepSeek are China-based and OpenRouter routes via a
> US intermediary — extraction sends names (deceased + heirs) outside the EU, a
> transfer with **no adequacy decision**. The owner has accepted this for
> cost/performance; the proper mitigation if revisited is a DPA + data minimisation,
> or an EU/US-hosted model for the name-bearing payloads.

This document justifies two architectural decisions for NadiaAI: (1) using a
cloud LLM API for production extraction instead of a local LLM, and (2) how the
solution sits against EU/Spanish data-protection law.

---

## 1. Why the Claude API in production

The heir/address extractor (`utils/extraction.py::extract_inheritance_data`) is
provider-agnostic and tries, in order: **Anthropic Claude → local Ollama → regex**.
In production it must run on Claude. Reasons:

1. **The cron has no local model.** The daily pipeline runs on GitHub Actions
   (`.github/workflows/daily.yml`, `ubuntu-latest`). There is no Ollama daemon on
   that runner, so every `localhost:11434` call fails and extraction silently
   degrades to the weak regex fallback. The strong local numbers we saw earlier
   came from runs on the developer's own machine (which has Ollama) — they never
   reflected what prod was actually producing. Claude runs anywhere a network call
   can be made, CI included, with no infra to provision.

2. **Quality on short, messy Spanish text.** The inputs are legal edicts and
   obituaries: dense, abbreviated, accented, and full of boilerplate that a regex
   happily mistakes for a name ("En Cualquier Caso" → fake heir). Claude reliably
   returns structured JSON (causante, heirs, date, address) and respects the schema;
   the regex path is a floor, not a substitute. Bad extractions poison the data
   (they already cost us our only Tier A lead once), so extraction quality is the
   product, not a nice-to-have.

3. **Cost is negligible at this volume.** Each lead is a few hundred tokens.
   Estimate ~$0.005–0.02/lead → roughly $10–40 to clear the full ~1,850 backlog and
   ~$15–70/month ongoing. `NADIA_ANTHROPIC_MODEL` lets us drop to
   `claude-haiku-4-5` (~5× cheaper) — ample for this short-text extraction — or keep
   `claude-opus-4-8` where quality matters most. This is far cheaper than running and
   babysitting a GPU box for an Ollama deployment that would still need to be online,
   patched, and scaled.

### Why not a local LLM (Ollama) in prod

- **It silently fails where it matters.** A local model only helps on a machine that
  is running it. Our scheduled execution environment is not that machine, and a
  failure mode that produces plausible-but-empty output (regex fallback) is worse
  than a loud error.
- **Operational cost outweighs the savings.** Self-hosting means a always-on host
  with enough VRAM, plus updates, monitoring, and a cold-start story — to save cents
  per run. Not worth it at this scale.
- **It's still useful as a fallback.** We keep Ollama in the chain for local dev and
  as a zero-marginal-cost option on machines that have it. It is a fallback, not the
  production path.

**Action required for prod:** set `ANTHROPIC_API_KEY` as a GitHub Actions secret
(already wired into `daily.yml`). Until that secret exists, the cron keeps falling
back to regex and heir/address coverage stays near zero. Optionally set
`NADIA_ANTHROPIC_MODEL=claude-haiku-4-5` to cut cost.

---

## 2. Data legality of the solution

We process two kinds of people:

- **Deceased persons** (the *causante*). Under GDPR these are largely out of scope —
  Recital 27 says the Regulation does not apply to the personal data of deceased
  persons. **But** Spain's LOPDGDD (Ley Orgánica 3/2018) **Art. 3** grants the
  deceased's heirs/relatives rights over that data, so it is not a free-for-all.
- **Living people** — heirs, family, business owners. These **are** personal data and
  GDPR fully applies. This is the population that actually matters legally, because
  outreach targets them.

### What works in our favour

- **Sources are official public bulletins.** BOE, BOA, BOP Zaragoza, Tablón de
  Edictos, BORME — these are state/regional publications made public by law. Lawful
  to access and process, and there is a plausible **legitimate-interest** basis
  (GDPR Art. 6(1)(f)) for B2B/real-estate prospecting built on public registries.
- **The pipeline already encodes restraint.** Each lead carries an
  `outreach_allowed` flag with `outreach_notes`; distress/auction signals are marked
  not-contactable, and auctions were dropped entirely as a lead source. Notary names
  are masked. Name-validation rejects boilerplate so we don't store junk about
  random third parties.
- **Data minimisation is feasible.** We only need name + locality + a death/legal
  signal to prospect. We are not assembling sensitive-category data (health,
  religion) — even though obituaries can hint at it, we don't store it.

### Where the real obligations / risks are

1. **Transparency (Arts. 13–14).** "Public source" ≠ "no duties". When data is
   obtained from a source other than the data subject and then used to contact them,
   GDPR Art. 14 requires giving them privacy-notice information (who we are, why,
   legal basis, their rights) **at the latest at first contact**. ✅ **Implemented**
   2026-06-15: `outreach.RGPD_NOTICE_FULL` is attached centrally to every outbound
   message in `render_outreach`, so no channel can ship a first contact without the
   notice (controller, purpose, legal basis, source, rights, AEPD).
2. **Legitimate-interest balancing test.** The Art. 6(1)(f) basis requires a
   documented LIA weighing our commercial interest against the individual's
   reasonable expectations — people in grief have a high privacy expectation.
   ✅ **Written** 2026-06-15: [LIA_legitimate_interest.md](./LIA_legitimate_interest.md).
   Its conclusion is binding on the pipeline: public-procedure cohorts (notarial/
   judicial) and FSBO pass; pure obituary/esquela contact does **not** clearly pass,
   so a death is a *watch* signal, not a contact trigger.
3. **Cross-border processing → Anthropic.** Sending names/localities to the Claude
   API is a transfer to a US processor. Needs a **Data Processing Agreement** and a
   valid transfer mechanism (SCCs / adequacy). Anthropic offers commercial terms with
   **zero data retention** for API traffic — use that tier so prompts aren't retained
   or used for training. Minimise what we send (name + city + the edict snippet; no
   need to ship full dossiers).
4. **Right to object & ePrivacy on outreach.** Individuals can object to
   legitimate-interest processing, and B2B/cold outreach is also governed by Spanish
   ePrivacy rules (LSSI) — email/phone outreach has its own consent/opt-out regime
   separate from GDPR. A suppression list must be honoured. ✅ **Implemented**
   2026-06-15: `nadia_ai.suppression` (do-not-contact ledger; `tools/suppress.py` to
   add an entry) is enforced as a gate in the outreach builder — every candidate is
   filtered before rendering, so an opt-out can never reappear in a worklist.
5. **PhantomBuster social enrichment is the sharpest edge.** Scraping public social
   profiles to attach a contact path materially increases privacy intrusion and
   platform-ToS risk. It is defensible for *named heirs of public inheritance edicts*
   far more than for *obituary mourners*. Keep it scoped to the legal-edict tiers and
   keep the LIA aligned with that scope.

### Bottom line

The ingestion side (public official bulletins, minimised storage, outreach flags,
masking) is on reasonably solid ground. The exposure was concentrated on the
**outreach and enrichment** end, and most of it is now closed in code/docs (2026-06-15):
the **Art. 14 notice** ships on every message, the **LIA** is written and binds the
pipeline to the defensible cohorts, and an **opt-out/suppression** gate is enforced.

**Remaining before scaled commercial outreach** (esp. selling the tool to other
agents, who become joint/independent controllers):
1. A **DPA + valid international-transfer mechanism** for the extraction LLM — note
   the processor is now **OpenRouter/MiniMax (non-EU)**, *not* Anthropic; either sign
   SCCs + minimise the payload, or move name-bearing extraction to an EU/US-hosted
   model. This is the sharpest open item.
2. A **qualified Spanish DPA-lawyer sign-off** on the LIA and the notice text.
3. If reselling: a controller/processor map per agent-client and a template DPA.

These are paperwork/contracts, not engineering blockers — but they should exist
before outreach scales beyond a supervised pilot.

---

> **Disclaimer:** This is an engineering decision record, not legal advice. The
> GDPR/LOPDGDD/LSSI analysis above is a good-faith summary to guide design; confirm
> with a qualified Spanish data-protection lawyer before commercial outreach at scale.
