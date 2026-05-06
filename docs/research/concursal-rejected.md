# Publicidad Concursal — Rejected as Data Source

**Decision date:** 2026-04-30

## What is Publicidad Concursal?

[publicidadconcursal.es](https://www.publicidadconcursal.es) is a Spanish public registry that aggregates insolvency proceedings (concursos de acreedores). It lists debtors who have entered formal insolvency, including individuals and companies.

## Why it was rejected

### 1. CAPTCHA protection blocks automated access

The site uses CAPTCHA challenges on search and result pages, making reliable automated scraping infeasible without resorting to CAPTCHA-solving services — which would be both fragile and ethically questionable.

### 2. Purpose-limitation clause in terms of service

The site's terms restrict the use of published data to specific legal purposes (consultation by creditors, courts, and administrators). Commercial reuse of this data for lead generation falls outside the permitted purposes and could expose the project to legal risk.

### 3. AEPD distress-topic doctrine (privacy concern)

This is the strongest reason for rejection. Under the AEPD's (Agencia Española de Protección de Datos) guidance and Art. 6.1.f RGPD (legitimate interest), the balancing test must consider the data subject's reasonable expectations and vulnerability:

- Persons listed in insolvency proceedings are in **financial distress**.
- Commercial outreach (even postal) to someone *because* they are insolvent constitutes processing of data that reveals a distress situation.
- The AEPD has consistently held that the legitimate-interest balance tips **against** the data controller when the data subject is in a vulnerable situation (financial distress, health, etc.).
- Sending a real-estate letter to someone whose insolvency is public knowledge would likely be perceived as exploitative, regardless of intent.

**Conclusion:** Even if the data is technically public, the privacy risk under Art. 6.1.f RGPD is unacceptable for this use case.

### 4. BOE Sección IV already covers the same ground

If concurso-related enrichment is ever needed (e.g., to flag that a property is involved in insolvency), it can be achieved by filtering BOE Sección IV publications for `<departamento>="JUZGADOS DE LO MERCANTIL"`. The BOE scraper module (`scrapers/boe.py`) already ingests Sección IV, so this filtering requires zero additional infrastructure.

This approach avoids the CAPTCHA problem, respects the legitimate-interest balance (since the data is used for enrichment/context, not direct outreach), and keeps the pipeline within official gazette sources.

## Summary

| Factor | Assessment |
|--------|------------|
| Technical feasibility | Blocked by CAPTCHA |
| Legal (ToS) | Commercial reuse restricted |
| Legal (RGPD Art. 6.1.f) | Fails legitimate-interest balance — distress topic |
| Alternative available | Yes — BOE Sección IV with mercantile court filter |
| Decision | **Rejected** |
