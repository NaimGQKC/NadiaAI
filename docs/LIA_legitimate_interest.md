# Legitimate Interest Assessment (LIA) — NadiaAI outreach

_Last updated: 2026-06-15. Engineering/compliance working document, **not legal
advice** — confirm with a Spanish data-protection lawyer before commercial outreach
at scale. See also [LLM_AND_DATA_LEGALITY.md](./LLM_AND_DATA_LEGALITY.md)._

NadiaAI processes the personal data of **living people** (heirs/relatives named in
public inheritance edicts, and FSBO property owners) to contact them about
real-estate services. The chosen Art. 6(1)(f) GDPR legal basis (**legitimate
interest**) requires this documented three-part test. It also **scopes who we may
contact** — the conclusion below is binding on the pipeline, not aspirational.

---

## 1. Purpose test — is there a legitimate interest?

Yes. A real-estate agent has a genuine, lawful commercial interest in identifying
property owners who are likely to sell and offering them brokerage services.
Inheritance of real property is a well-established trigger for a sale (heirs
typically liquidate rather than co-own; plusvalía/IBI deadlines push a decision).
The interest is specific, present, and not speculative.

## 2. Necessity test — is the processing necessary?

Yes, and it is **data-minimised**:
- We process only **name + locality + the public legal signal** (the edict and its
  procedure). That is the minimum needed to identify and respectfully approach a
  likely seller.
- We do **not** assemble special-category data (health, religion, etc.). Obituaries
  can hint at it; we do not store it.
- There is no less-intrusive way to reach a named heir of a specific estate. Generic
  advertising cannot target "the heirs of this particular property".

## 3. Balancing test — does our interest override the individual's rights?

This is where the line is drawn. The individual's reasonable expectations and the
intrusiveness of contact differ sharply by source:

| Cohort | Reasonable expectation | Intrusiveness | Verdict |
|---|---|---|---|
| **Notarial heir-declaration** (BOE-N) | Heirs actively initiated a *public* legal procedure to be declared; they expect administrative consequences | Postal letter, no grief intrusion | **Proceed** — interest prevails |
| **Judicial herencia yacente** (TEJU) | Heirs unknown/absent; nobody to weigh against yet | We only *watch*; contact the office, not a person | **Proceed (monitor only)** |
| **FSBO owner** (pisos.com) | Owner published an ad *soliciting* contact about selling | Contact is exactly what they invited | **Proceed** — interest prevails |
| **Obituary / esquela** (Memora, Heraldo, defunciones) | A grieving family with a **high** privacy expectation, who took **no** legal/commercial step | Cold contact soon after a death is intrusive | **Restrict** — does *not* clearly prevail |

### Safeguards that tip the balance in our favour
- **Art. 14 privacy notice at first contact** — controller, purpose, legal basis,
  source, and how to object — is attached to every outbound message centrally
  (`outreach.RGPD_NOTICE_FULL`), so no channel can ship without it.
- **A working opt-out.** The promise "puede solicitar su supresión" is honoured by a
  real suppression list (`nadia_ai.suppression`); the outreach pack runs every
  candidate through it before rendering. One objector in a family suppresses the lead.
- **Channel/timing restraint encoded in the product.** Obituary leads are routed to
  *letter only, after 30–60 days* (or, better, held until a heir-declaration edict
  appears); auctions were dropped entirely; notary/court names are for verification,
  not captación.
- **No third-party sharing; minimised retention** (24-month person TTL).

### Conclusion (binding on the pipeline)
- **Public-procedure cohorts (notarial, judicial) and FSBO: legitimate interest is a
  sound basis.** Proceed, with the Art. 14 notice + suppression gate.
- **Pure obituary/esquela cohorts: the balance does not clearly favour us.** Treat the
  death as a *watch* signal, not a contact trigger — do not cold-contact a grieving
  family on the strength of an obituary alone. Prefer waiting for the heir-declaration
  edict (which moves them into the "notarial" cohort and flips the balance).

## Review
Re-run this LIA if we change sources, channels, timing, or add social-profile
enrichment (PhantomBuster) — the last materially increases intrusiveness and must
stay scoped to the named-heir legal-edict cohorts, never obituary mourners.
