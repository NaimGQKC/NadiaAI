# BOE Sección II.B (Notario Nombramientos) — Rejected as Data Source

**Decision date:** 2026-04-30

## What is Sección II.B?

BOE Sección II.B publishes official personnel announcements for the Spanish judicial and notarial system. This includes:

- **Appointments** (nombramientos) of notaries and judicial officials
- **Cessations** (ceses), including retirement, transfer, and death
- **Nominations** and competitive exam results (oposiciones)

## Why it was considered

During source discovery for Phase 2, Sección II.B was flagged because the term "cese por defuncion" appears in some entries — indicating that a notary has died. Since death is the triggering event for inheritance leads, this warranted investigation.

## Why it was rejected

### 1. The deceased is a notary, not a property-owning causante

Sección II.B records the death of a notary in their capacity as a public official. The purpose of the publication is administrative: to formally vacate the notarial seat so it can be reassigned.

This is fundamentally different from an inheritance edict, which identifies a causante (deceased person) whose estate is being distributed. A Sección II.B entry tells us nothing about:

- Whether the notary owned property
- Where that property is located
- Whether the estate is being processed
- Who the heirs are

### 2. Zero lead value

Identifying that a notary has died does not produce an actionable lead. There is no property reference, no heir information, and no connection to an inheritance proceeding. The information is purely about the vacancy of a public office.

### 3. Redundancy with actual inheritance sources

If a deceased notary's estate enters the inheritance system (as any citizen's would), it will appear through the normal channels:

- **Tablón de Edictos** — if the declaration of heirs is processed in Zaragoza
- **BOE TEJU** — if the case is published in the Tribunal notices
- **BOA** — if a Junta Distribuidora is convened (rare, for estates without known heirs)

These sources provide the actual inheritance data (causante name, proceeding details, sometimes property references) that Sección II.B lacks entirely.

### 4. Signal-to-noise ratio

Sección II.B publishes hundreds of entries about notary transfers, appointments, and exam results. Deaths are a tiny fraction. Scraping and filtering this section for the rare "cese por defuncion" would add complexity with no return.

## Summary

| Factor | Assessment |
|--------|------------|
| Lead value | None — no property, heir, or estate information |
| Redundancy | Deaths that matter will appear in TEJU, Tablon, or BOA |
| Signal-to-noise | Very low — mostly appointments and transfers |
| Implementation cost | Low, but pointless |
| Decision | **Rejected** |
