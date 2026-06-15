# Resolving an Heir's Contact from Name + Locality — Honest Method Report

_Last updated: 2026-06-15. Not legal advice — see the disclaimer at the bottom._

This is the project's central business question: we reliably extract an heir's
**NAME + locality** from public BOE notarial inheritance declarations, but a name
is not a contact. This document assesses, honestly, every realistic legal-ish way
to turn that name + locality into a way for a real-estate agent to *reach* the
person, with a coverage / cost / legality verdict for each and a recommended path.

> **TL;DR (the honest verdict).** There is **no** path that turns an arbitrary
> Spanish name + city into a phone number at scale. The realistic, defensible
> contact strategy is, in priority order:
> **(1) the notaría handling the declaración as the legal intermediary (~100% of
> BOE-N leads, free, already captured), (2) a postal letter to the last-known
> locality (always available, deterministic, the floor), and (3) — only as a paid,
> gated add-on — a Registro de la Propiedad name→property lookup (high precision
> with a NIF, ~€9/finca, needs interés legítimo) or a skip-trace broker (modest
> cold-name coverage, needs a DPA).** Direct phone/email of a private heir is the
> exception, not the plan.

The code that implements this lives in `src/nadia_ai/contact_resolve.py` (a
`ContactResolver` protocol + a waterfall that always falls back to postal mail).

---

## Method-by-method assessment

### 1. Public phone / email directory by name (páginas blancas, QDQ) — DEAD

- **Coverage: ~0%.** The last Spanish residential white-pages edition was **2012**.
  The regime flipped from opt-out to **opt-in**: an individual's number is not
  listed unless they actively asked to be. The "páginas blancas" lookalike sites
  still online are stale scrapes, not an official, queryable directory.
- **Cost:** n/a.
- **Legality:** querying a private individual's number by name is not a service the
  law supports; numbers of people who opted out are explicitly withheld (only
  emergency services get them).
- **Verdict:** matches our prior measured finding that web/LLM search for an
  individual heir's phone yields ~0%. **Do not rebuild.** Not implemented.

### 2. Censo electoral (electoral roll) — BARRED

- **Coverage:** would be ~100% (every adult is on it) — but it is **off-limits**.
- **Legality:** **LOREG Art. 41** prohibits *particularised* access to census
  personal data except by judicial channel. Copies handed to electoral candidacies
  are usable **only** for the electoral campaign and **must be deleted** afterward;
  commercial use is expressly excluded. The AEPD treats misuse as a serious breach.
- **Verdict:** legally unusable for lead-gen. **Not implemented.**

### 3. Registro de la Propiedad — name → property → owner (the strong deterministic path)

Two related products, uniquely apt for inheritance because the heir is becoming the
registered owner of inherited property:

- **Servicio de Índices / nota de localización (name → where they own):** searches
  the **Índice General Informatizado de fincas y derechos** nationwide and returns
  the registers/municipalities where a named person holds fincas or rights. Input:
  **name + apellidos and DNI/NIF**. Without the NIF, common names return multiple
  titulares (homonym problem again).
- **Nota simple (property → owner detail):** ~**€9.02/finca** (online, +taxes;
  urgent variants €10–25). Returns the current registered owner, charges, and the
  registered context — and can be requested **by titular name (+NIF)** as well as by
  finca/RC.
- **Coverage:** **high precision when a NIF is known**; medium when only a name is
  known (homonyms). TEJU judicial edicts sometimes print a **partial NIF** for named
  parties (parsed by `utils/edict_parse`), which materially helps disambiguation.
- **Cost:** ~€9/finca + the per-query Índices fee; paid per lead.
- **Legality:** access is **gated on a register-vetted *interés legítimo*** — the
  requester must state and justify the interest, which the Registrador evaluates. A
  commercial real-estate prospecting interest is plausible but **not guaranteed to
  pass**, and feeds straight into the LIA. Defensible for the public-procedure
  (notarial/judicial) cohorts the LIA already blesses; weak for obituary-only leads.
- **Verdict:** **the best deterministic name→address path we have**, but paid and
  legally gated → a **Phase-2, env-keyed provider**. Implemented as a **stub**
  (`registry_resolver`, gated on `REGISTRO_INDICES_API_KEY`, off by default).

### 4. Paid skip-trace / "localización de personas" broker — modest, heavy

- **What it is:** Spain has no US-style self-serve "name in → phone out" API for
  consumers. The equivalent is **debt-collection / detective-grade *localización de
  personas*** (e.g. firms in the cobro-de-impagados space) that cross-reference
  padrón, property, vehicle and mercantil registries under a claimed legitimate
  interest, plus **B2C list/listbroking** houses (e.g. Publiactivos, ~15M records)
  that rent *campaigns* rather than expose arbitrary lookups.
- **Coverage:** **modest on a cold individual name.** List houses keep consent
  control and sell segments, not a specific person's number; investigator services
  can sometimes resolve a current address but quote per-case and skew toward debtor
  scenarios. Realistically a **minority** of named heirs resolve to a verified
  phone, and homonym risk persists.
- **Cost:** paid per lookup / per case (no public flat rate; investigator work is
  the pricier end).
- **Legality:** the **heaviest** of the viable options — leans on registry/padrón
  cross-refs, needs a documented legitimate interest and a **DPA** with the
  provider, and a cold sales call off a skip-traced mobile is the kind of contact
  most exposed under RGPD + LSSI.
- **Verdict:** the **only** realistic *phone* path, but low-yield and legally
  involved → keep it **gated, off by default, one provider slot**. Implemented as a
  **stub** (`skiptrace_resolver`, gated on `CONTACT_SKIPTRACE_API_KEY`).

### 5. Notaría handling the declaración — the intermediary (already captured)

- **Why it works where heir-search fails:** the notary running a *declaración de
  herederos ab intestato* is a **public office**, competent at the deceased's last
  domicile, **required to deal with interested parties** and to publish the
  proceeding (which is exactly how we found the lead in BOE). The office name (and
  often phone) is already parsed into the lead's `juzgado`/`contact_phone` by
  `utils/edict_parse` and `enrich_contact.resolve_office_phones`.
- **Coverage:** **~100% of BOE-N notarial leads** have a named handling office; it
  is public and verifiable.
- **Cost:** free.
- **Legality:** the **cleanest** path — contacting the public office that is *itself
  the legal channel to the heirs* is far more defensible than cold-calling a
  scraped personal number. It is the agent's point of entry, **not** the heir's
  private line, and that distinction must be respected in the worklist copy.
- **Verdict:** **the warmest and most defensible channel for the notarial cohort.**
  Implemented as `notary_resolver` (returns the office; HIGH confidence; flagged
  indirect).

### 6. Postal mail to the last-known locality — the always-available floor

- **Coverage:** **always constructable** from name + locality. Correos delivers on
  `"Nombre Apellidos, <localidad>, España"`; a full street address (when an edict
  gives an *último domicilio*) makes it door-deliverable.
- **Cost:** the price of a stamp.
- **Legality:** a postal letter to a **named heir of a public inheritance edict**,
  carrying the **RGPD Art. 14 notice** (already attached centrally by
  `outreach.RGPD_NOTICE_FULL`), is the channel the project's LIA already treats as
  the defensible first contact. No telephone/ePrivacy consent issue, low intrusion.
- **Verdict:** **the deterministic floor the product leans on.** Implemented as
  `postal_resolver`; it never declines a validly-named lead, which is what
  guarantees the waterfall always returns *something*.

---

## Coverage summary

| Method | Realistic coverage | Cost | Legality | Status in code |
|---|---|---|---|---|
| White-pages / phone-by-name | ~0% | — | unsupported (opt-in) | not built (known-dead) |
| Censo electoral | n/a | — | **barred** (LOREG 41) | not built |
| Registro Índices + nota simple | high **with NIF**, else med | ~€9/finca + fee | interés legítimo gated | **stub** (env-keyed, off) |
| Skip-trace / localización broker | modest on cold name | paid/case | heaviest; needs DPA | **stub** (env-keyed, off) |
| **Notaría intermediary** | **~100% of BOE-N** | free | **cleanest** | **implemented** |
| **Postal mail** | **always** | stamp | defensible (Art. 14) | **implemented** |

---

## Recommended contact strategy

1. **For notarial (BOE-N) and judicial (TEJU) leads — the engine cohort —** route
   the agent to the **notaría/juzgado** as the legal intermediary (channel
   `notary`). It is free, ~100% available, and the most defensible first move.
2. **For every validly-named lead**, always have the **postal target** (channel
   `postal`) as the fallback so no lead is ever "uncontactable". Send the Art. 14
   letter to the last domicile (HIGH confidence) or the locality (MEDIUM).
3. **Do not** spend on web/LLM phone search for individual heirs (`~0%`,
   `CONTACT_ENRICH_MAX_PER_RUN=0` already reflects this).
4. **Phase 2, only for high-value cohorts and only once a DPA / interés-legítimo
   wording exists:** turn on the **Registro Índices** provider (name+NIF → property
   → registered domicile; the strongest paid path) and/or a single **skip-trace**
   provider for a hand-picked phone push. Both are env-gated and off until then.

The honest bottom line: **the product's contactability moat is speed +
notaría-as-intermediary + a deterministic postal letter with AI-generated copy —
not a magic phone number.** Selling on "we get you the heir's address/route to the
estate fast, compliantly" is true; selling on "we get you their mobile" is not.

---

## How it's wired (see `contact_resolve.py`)

- A `ContactResolver` protocol: `(name, locality, *, address, ref_catastral,
  office) -> ResolvedContact | None`.
- A `ResolvedContact` carries the `channel`, the actionable `target`, a coarse
  `confidence` (high/medium/low, matching `enrich_contact`), a `source`, an optional
  `source_url` citation, and human `instructions` for the worklist. `is_direct`
  marks phone/email vs the indirect registry/notary/postal channels.
- `resolve_contact` runs providers in priority order
  (`skiptrace → registry → notaria → postal`), stops on the first hit, isolates a
  raising provider, and — because **postal is always last and only declines a blank
  name** — never returns empty for a real lead.
- New paid providers slot in as a one-line entry in `DEFAULT_RESOLVERS`, behind an
  env-keyed flag in `config.py` (`REGISTRO_INDICES_API_KEY`,
  `CONTACT_SKIPTRACE_API_KEY`), exactly like the existing `EINFORMA_API_KEY` stub.

---

> **Disclaimer:** This is an engineering decision record, not legal advice. The
> RGPD/LOPDGDD/LOREG/LSSI analysis is a good-faith summary to guide design; confirm
> with a qualified Spanish data-protection lawyer before commercial outreach at
> scale, and obtain a DPA before enabling any paid provider above.
