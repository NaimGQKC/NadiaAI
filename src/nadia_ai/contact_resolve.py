"""Turn an heir's NAME + locality into a usable CONTACT path.

This is the project's central business problem isolated into one pluggable place.
`enrich_contact.py` already runs a *search-LLM* waterfall for a phone/email; this
module is the layer above it — a provider abstraction that answers the harder,
honest question "how does the agent actually *reach* this person?" and that, unlike
the search-LLM, is engineered to NEVER return empty: it always degrades to a
deterministic postal-mail target.

Why a new abstraction instead of more sources in enrich_contact's waterfall:
  * The realistic Spanish contact paths are not all "phone/email" (see
    docs/contact_methods.md). The two that actually work are *deterministic*:
      1. write a letter to the person at their last-known locality, and
      2. go through the **notaría** handling the declaración (a public office we
         already capture in `juzgado`) as the legal intermediary.
    Both are address/office paths, not a scraped mobile number, so they don't fit
    the phone-shaped ContactResult and want their own typed result.
  * Paid providers (a property-registry *Servicio de Índices* lookup, or a
    skip-trace broker) slot in behind an env-keyed flag, exactly like the
    `EINFORMA_API_KEY` stub already does in config — off by default, no real key.

The honest coverage verdict (full write-up in docs/contact_methods.md):
  - Public phone/email directory by name  → ~0% (páginas blancas dead since 2012,
    opt-in regime; censo electoral barred for commercial use). Known-dead; we don't
    rebuild it here.
  - Registro de la Propiedad name→property (Servicio de Índices + nota simple)
    → high precision *with a NIF*, gated on a register-vetted interés legítimo and
    paid (~€9/finca). Env-keyed provider stub; the real call is a Phase-2 contract.
  - Notaría intermediary → ~100% reachable for notarial (BOE-N) leads, because the
    office is public; it's the legal point of entry, not the heir's personal line.
  - Postal mail to last locality → always constructable from name+locality; the
    deterministic floor the product already leans on.

Design: a `ContactResolver` Protocol; each provider takes (name, locality,
address, ref_catastral) and returns a confidence-scored `ResolvedContact` or None
("can't help — defer to the next"). `resolve_contact` runs them in priority order
and ALWAYS appends the postal resolver last so the waterfall cannot come back
empty. Pure functions of their inputs — no DB, no live network in the default
(off) configuration — so they unit-test without either.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from nadia_ai.config import CONTACT_SKIPTRACE_API_KEY, REGISTRO_INDICES_API_KEY
from nadia_ai.merge import normalize_name

logger = logging.getLogger("nadia_ai.contact_resolve")


# How to reach the person. Ordered loosely best→fallback so a caller can rank.
# A "channel" is what the agent physically does, not a guarantee of a live number.
CHANNEL_PHONE = "phone"            # a personal phone (only paid providers, rarely)
CHANNEL_EMAIL = "email"            # a personal email
CHANNEL_REGISTRY = "registry"      # an address resolved via the property registry
CHANNEL_NOTARY = "notary"          # the notaría/juzgado handling the procedure
CHANNEL_POSTAL = "postal"          # a letter to the last-known locality (always on)

# Confidence is coarse on purpose — it mirrors enrich_contact's high/medium/low
# vocabulary so a downstream sort/threshold treats both modules the same.
CONF_HIGH = "high"
CONF_MEDIUM = "medium"
CONF_LOW = "low"


@dataclass
class ResolvedContact:
    """One resolver's answer: a reachable channel for this person.

    A resolver returns this on a hit and ``None`` to defer to the next provider.
    `target` is the channel-specific destination the agent acts on — a phone
    string, an email, a postal address line, or the notaría's name — and
    `instructions` is a one-line human hint for the worklist (e.g. "Llamar a la
    notaría y preguntar por el expediente"). Nothing here is written to the live
    DB by this module; the caller decides how to persist it.
    """

    channel: str
    target: str
    confidence: str = CONF_MEDIUM
    source: str = ""                      # which resolver produced it
    source_url: str = ""                  # citation / provider reference, if any
    instructions: str = ""                # how the agent should use `target`
    meta: dict = field(default_factory=dict)

    @property
    def is_direct(self) -> bool:
        """True for a personal phone/email — the only channels that reach the heir
        directly. Registry/notary/postal are indirect (an address or a gatekeeper),
        which is the honest shape of the Spanish problem."""
        return self.channel in (CHANNEL_PHONE, CHANNEL_EMAIL)


@runtime_checkable
class ContactResolver(Protocol):
    """A pluggable contact-resolution provider.

    Implementations are callables (a function or any object with ``__call__``) so a
    plain function satisfies the protocol — matching how `enrich_contact` registers
    bare functions in its waterfall. Return a `ResolvedContact` on a hit or ``None``
    to defer (wrong lead type, not configured, transient, or nothing found)."""

    def __call__(
        self,
        name: str,
        locality: str,
        *,
        address: str | None = None,
        ref_catastral: str | None = None,
        office: str | None = None,
    ) -> ResolvedContact | None: ...


# ── Resolver: postal mail (the always-available floor) ──────────────────────
# The one path that needs no provider, no key, and no network: a person with a
# name and a locality can be written to. Spain has no public phone directory, but
# Correos delivers on "Nombre Apellidos, <localidad>, España" and the LIA already
# treats a postal letter to a named heir of a public edict as the defensible
# first-contact channel. This resolver therefore never returns None for a valid
# name — it is what guarantees the waterfall always produces *something*.


def postal_resolver(
    name: str,
    locality: str,
    *,
    address: str | None = None,
    ref_catastral: str | None = None,
    office: str | None = None,
) -> ResolvedContact | None:
    """Build a deterministic postal-mail target from name + locality.

    Confidence reflects address precision, not identity:
      * a full street `address` → HIGH (deliverable to the door),
      * locality only → MEDIUM (deliverable, the post office routes by name),
      * no usable name → None (can't even address an envelope; let the waterfall
        end empty rather than print a blank label).
    """
    clean = normalize_name(name)
    if not clean:
        return None  # nothing addressable — the only case postal can't serve

    # Re-display the name as given (normalize_name lower-cases for matching).
    display_name = " ".join(name.split()).strip()
    loc = (locality or "").strip()

    if address and address.strip():
        line = f"{display_name}, {address.strip()}"
        if loc:
            line += f", {loc}"
        confidence = CONF_HIGH
    elif loc:
        line = f"{display_name}, {loc}, España"
        confidence = CONF_MEDIUM
    else:
        # Name but no locality — still addressable in principle but very weak.
        line = f"{display_name}, España"
        confidence = CONF_LOW

    return ResolvedContact(
        channel=CHANNEL_POSTAL,
        target=line,
        confidence=confidence,
        source="postal",
        instructions="Enviar carta de presentación (con cláusula RGPD art. 14) al domicilio indicado.",
        meta={"locality": loc, "has_street": bool(address and address.strip())},
    )


# ── Resolver: notaría / juzgado intermediary ────────────────────────────────
# The warmest path for notarial (BOE-N) and judicial (TEJU) leads, and the reason
# it works where heir-phone search fails: the handling office is a PUBLIC body that
# is *required* to deal with interested parties to the estate. We already capture
# its name (and often phone, via edict_parse) in the lead's `juzgado`/contact
# fields. This resolver surfaces that office as the legal contact channel — the
# agent's point of entry, explicitly NOT the heir's personal line.


def notary_resolver(
    name: str,
    locality: str,
    *,
    address: str | None = None,
    ref_catastral: str | None = None,
    office: str | None = None,
) -> ResolvedContact | None:
    """Surface the notaría/juzgado handling the declaración as the contact channel.

    Returns None when no handling office is known (most non-edict leads), so it
    self-selects to exactly the notarial/judicial cohort. Confidence is HIGH: the
    office is public and verifiable, even though it only *indirectly* reaches the
    heir."""
    if not office or not office.strip():
        return None
    return ResolvedContact(
        channel=CHANNEL_NOTARY,
        target=office.strip(),
        confidence=CONF_HIGH,
        source="notaria",
        instructions=(
            "Contactar con la notaría/juzgado que tramita la declaración de "
            "herederos; es el punto de entrada legal hacia los interesados."
        ),
        meta={"locality": (locality or "").strip()},
    )


# ── Resolver: property registry name→address (Servicio de Índices) ──────────
# The strongest *deterministic* path to an heir's whereabouts, and uniquely apt
# for inheritance: the heir is about to become the registered owner of inherited
# property. The Registro de la Propiedad's Servicio de Índices resolves a NAME
# (best disambiguated by NIF) to the registers/municipalities where that person
# holds fincas; a follow-up nota simple (~€9) then returns the property + owner
# detail. BUT access is gated on a register-vetted *interés legítimo* and is paid,
# so this is an env-keyed provider STUB — off by default, no real key, no network —
# exactly like the EINFORMA stub. When a key + an implementation are added it slots
# into the waterfall ahead of postal.


def registry_resolver(
    name: str,
    locality: str,
    *,
    address: str | None = None,
    ref_catastral: str | None = None,
    office: str | None = None,
) -> ResolvedContact | None:
    """Resolve name → property/address via the Registro Servicio de Índices.

    STUB: returns None unless ``REGISTRO_INDICES_API_KEY`` is set (it never is by
    default). The real implementation would submit the name (+NIF when known) under
    a documented interés legítimo, then optionally pull a nota simple for the finca.
    Until then it defers, so the waterfall falls through to postal."""
    if not REGISTRO_INDICES_API_KEY:
        return None
    clean = normalize_name(name)
    if not clean:
        return None
    # TODO: call the Servicio de Índices (e.g. via a registradores.org gateway or a
    # reseller such as eInforma/Axesor), parse the registers where the person holds
    # fincas, and—if a finca matches the inherited property—pull a nota simple to
    # confirm the registered domicile. Return CHANNEL_REGISTRY with the resolved
    # address and the official reference as source_url. Left unimplemented on
    # purpose: it costs money per lookup and needs the interés-legítimo wording.
    return None


# ── Resolver: paid skip-trace broker (env-gated stub) ───────────────────────
# The closest thing to a US-style "name in → phone out" API. In Spain these are
# debt-collection / detective-grade *localización de personas* services (not a
# self-serve directory); they exist but are paid, lower-coverage on a cold name,
# and legally heavier (they lean on padrón/registry cross-refs under a claimed
# legitimate interest). One provider, env-keyed, off by default — the slot a real
# contract would fill.


def skiptrace_resolver(
    name: str,
    locality: str,
    *,
    address: str | None = None,
    ref_catastral: str | None = None,
    office: str | None = None,
) -> ResolvedContact | None:
    """Resolve name+locality → phone/address via a paid skip-trace provider.

    STUB: returns None unless ``CONTACT_SKIPTRACE_API_KEY`` is set. No real call,
    no key shipped. Documented here as the single paid phone-path slot; coverage on
    a cold Spanish individual is modest and the legal posture needs a DPA, so it
    stays gated."""
    if not CONTACT_SKIPTRACE_API_KEY:
        return None
    clean = normalize_name(name)
    if not clean:
        return None
    # TODO: POST {name, locality, address?} to the provider, map a verified hit to
    # ResolvedContact(channel=CHANNEL_PHONE/CHANNEL_REGISTRY, ...) with the
    # provider's confidence and a reference id in source_url. Drop low-confidence /
    # homonym-ambiguous results (same discipline as enrich_contact's identity gate).
    return None


# Priority order: cheapest / highest-precision / most-defensible first, with the
# always-available postal resolver LAST so `resolve_contact` can never return
# empty. Registry + skip-trace are gated stubs (return None until env-keyed), so by
# default the effective order is notaría → postal. Adding a real provider is a
# one-line edit here, mirroring enrich_contact.CONTACT_SOURCES.
DEFAULT_RESOLVERS: list[tuple[str, ContactResolver]] = [
    ("skiptrace", skiptrace_resolver),   # paid phone path (stub, off)
    ("registry", registry_resolver),     # paid name→property (stub, off)
    ("notaria", notary_resolver),        # public intermediary (free, edict leads)
    ("postal", postal_resolver),         # deterministic floor (always on)
]


def resolve_contact(
    name: str,
    locality: str,
    *,
    address: str | None = None,
    ref_catastral: str | None = None,
    office: str | None = None,
    resolvers: list[tuple[str, ContactResolver]] | None = None,
) -> ResolvedContact | None:
    """Run the resolver waterfall for one person and return the first hit.

    Tries providers in priority order, stops on the first non-None result, and —
    because `postal_resolver` is always last and only declines on a blank name —
    returns a usable channel for any validly-named lead. Returns None only when the
    name is unusable (so even postal can't address an envelope).

    `resolvers` overrides the default chain (used by tests and for a targeted run).
    """
    chain = resolvers if resolvers is not None else DEFAULT_RESOLVERS
    for _name, resolver in chain:
        try:
            result = resolver(
                name, locality, address=address, ref_catastral=ref_catastral, office=office
            )
        except Exception as e:  # a provider must never break the waterfall
            logger.warning("Contact resolver %s raised, skipping: %s", _name, e)
            continue
        if result is not None:
            return result
    return None


def resolve_for_lead(
    lead: dict, resolvers: list[tuple[str, ContactResolver]] | None = None
) -> ResolvedContact | None:
    """Convenience wrapper: pull the contact-relevant fields off a lead dict and
    resolve. Mirrors `enrich_contact.enrich_lead`'s field selection (heir first,
    then causante; locality; the handling office in `juzgado`) so the two modules
    read a lead the same way."""
    name = (lead.get("heir_name") or lead.get("causante") or "").strip()
    locality = (lead.get("localidad") or "").strip()
    address = (lead.get("direccion") or "").strip() or None
    rc = (lead.get("ref_catastral") or lead.get("referencia_catastral") or "").strip() or None
    office = (lead.get("juzgado") or "").strip() or None
    if not name:
        return None
    return resolve_contact(
        name, locality, address=address, ref_catastral=rc, office=office, resolvers=resolvers
    )
