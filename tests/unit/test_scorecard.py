"""Actionability scorecard tests."""

from nadia_ai.scorecard import compute_scorecard


def _seed(conn, **kw):
    cols = "tier region localidad direccion referencia_catastral heir_name contact_phone".split()
    vals = [kw.get(c, "") for c in cols]
    conn.execute(
        f"INSERT INTO leads ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals
    )
    conn.commit()


def test_scorecard_funnel_and_ccaa(db_conn):
    # Sevilla (Andalucía): Tier A, heir + street address → ready.
    _seed(db_conn, tier="A", region="Sevilla", direccion="Calle Sierpes 23",
          heir_name="Ana Ruiz")
    # Zaragoza (Aragón): Tier B, heir + phone → ready, but no street address.
    _seed(db_conn, tier="B", region="Zaragoza", direccion="Zaragoza",
          heir_name="Luis Gil", contact_phone="976123456")
    # Madrid: Tier B, no heir, no address → not ready.
    _seed(db_conn, tier="B", region="Madrid", localidad="Madrid")

    sc = compute_scorecard(db_conn)
    o = sc["overall"]
    assert o["total"] == 3
    assert o["A"] == 1 and o["B"] == 2
    assert o["heir"] == 2          # two leads have a named heir
    assert o["address"] == 1       # only the Sevilla one has a street number
    assert o["ready"] == 2         # Sevilla (addr) + Zaragoza (phone)

    by = sc["by_ccaa"]
    assert by["Andalucía"]["ready"] == 1
    assert by["Aragón"]["ready"] == 1
    assert by["Madrid"]["ready"] == 0
